#![cfg(windows)]
#![allow(clippy::useless_conversion)]

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use windows::core::PCSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE, LUID};
use windows::Win32::Security::{
    AdjustTokenPrivileges, LookupPrivilegeValueW, LUID_AND_ATTRIBUTES, SE_PRIVILEGE_ENABLED,
    TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES, TOKEN_QUERY,
};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::Win32::System::ProcessStatus::{
    EmptyWorkingSet, GetPerformanceInfo, GetProcessMemoryInfo, PERFORMANCE_INFORMATION,
    PROCESS_MEMORY_COUNTERS,
};
use windows::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
use windows::Win32::System::Threading::{
    GetCurrentProcess, GetCurrentProcessId, OpenProcess, OpenProcessToken,
    PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SET_QUOTA,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

#[allow(dead_code)]
mod cleaning;

// ----- RAII 句柄守卫 ------------------------------------------------------

/// RAII 包装器，确保 Win32 HANDLE 在 drop 时关闭，
/// 避免提前返回或 panic 时泄漏句柄。
struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.0.is_invalid() {
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }
}

// ----- NtQuerySystemInformation 结构体 ------------------------------------

const SYSTEM_PROCESS_INFORMATION_CLASS: i32 = 5;
const SYSTEM_MEMORY_LIST_INFORMATION_CLASS: i32 = 80;
const SYSTEM_FILE_CACHE_INFORMATION_EX_CLASS: i32 = 81;
const MEMORY_EMPTY_WORKING_SETS: u32 = 2;
const MEMORY_FLUSH_MODIFIED_LIST: u32 = 3;
const MEMORY_PURGE_STANDBY_LIST: u32 = 4;

#[repr(C)]
struct UnicodeString {
    length: u16,
    maximum_length: u16,
    buffer: *const u16,
}

/// x64 Windows 的 SYSTEM_PROCESS_INFORMATION 布局（自 Win2K 起稳定）。
#[repr(C)]
struct SystemProcessInformation {
    next_entry_offset: u32,
    number_of_threads: u32,
    working_set_private_size: u64,
    hard_fault_count: u32,
    number_of_threads_high_watermark: u32,
    cycle_time: u64,
    create_time: i64,
    user_time: i64,
    kernel_time: i64,
    image_name: UnicodeString,
    base_priority: i32,
    unique_process_id: usize,
    inherited_from_unique_process_id: usize,
    handle_count: u32,
    session_id: u32,
    unique_process_key: usize,
    peak_virtual_size: usize,
    virtual_size: usize,
    page_fault_count: u32,
    peak_working_set_size: usize,
    working_set_size: usize,
    quota_peak_paged_pool_usage: usize,
    quota_paged_pool_usage: usize,
    quota_peak_non_paged_pool_usage: usize,
    quota_non_paged_pool_usage: usize,
    pagefile_usage: usize,
    peak_pagefile_usage: usize,
    private_page_count: usize,
    read_operation_count: i64,
    write_operation_count: i64,
    other_operation_count: i64,
    read_transfer_count: i64,
    write_transfer_count: i64,
    other_transfer_count: i64,
}

#[repr(C)]
#[derive(Default)]
struct MemoryListInformation {
    zero_page_count: usize,
    free_page_count: usize,
    modified_page_count: usize,
    modified_no_write_page_count: usize,
    bad_page_count: usize,
    page_count_by_priority: [usize; 8],
    repurposed_pages_by_priority: [usize; 8],
    modified_page_count_page_file: usize,
}

#[repr(C)]
#[derive(Default)]
struct SystemFileCacheInformation {
    current_size: usize,
    peak_size: usize,
    page_fault_count: u32,
    minimum_working_set: usize,
    maximum_working_set: usize,
    current_size_including_transition_in_pages: usize,
    peak_size_including_transition_in_pages: usize,
    transition_repurpose_count: u32,
    flags: u32,
}

// ----- 辅助函数 -----------------------------------------------------------

fn wide_to_string(buf: &[u16]) -> String {
    let len = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    String::from_utf16_lossy(&buf[..len])
}

fn normalize_process_name(name: &str) -> String {
    cleaning::normalize_process_name(name)
}

fn parse_excluded_names(raw: &str) -> HashSet<String> {
    cleaning::parse_excluded_names(raw)
}

static EXCLUDED_CACHE: Mutex<Option<HashMap<String, HashSet<String>>>> = Mutex::new(None);

/// 缓存解析后的排除集合，让相同配置字符串的重复清理
/// 不必每次都重新解析逗号分隔列表。
fn parse_excluded_names_cached(raw: &str) -> HashSet<String> {
    if raw.is_empty() {
        return HashSet::new();
    }
    let mut guard = EXCLUDED_CACHE.lock().unwrap();
    if let Some(cache) = guard.as_ref() {
        if let Some(set) = cache.get(raw) {
            return set.clone();
        }
    } else {
        *guard = Some(HashMap::new());
    }
    let result = parse_excluded_names(raw);
    if let Some(cache) = guard.as_mut() {
        if cache.len() >= 50 {
            cache.clear();
        }
        cache.insert(raw.to_string(), result.clone());
    }
    result
}

fn cleaning_mode_min_ws(mode: &str) -> u64 {
    cleaning::cleaning_mode_min_ws(mode)
}

fn protected_names_for_mode(mode: &str) -> HashSet<&'static str> {
    cleaning::protected_names_for_mode(mode)
}

fn foreground_pid() -> Option<u32> {
    unsafe {
        let hwnd = GetForegroundWindow();
        if hwnd.0.is_null() {
            return None;
        }
        let mut pid = 0u32;
        let _ = GetWindowThreadProcessId(hwnd, Some(&mut pid));
        (pid != 0).then_some(pid)
    }
}

/// 返回可用物理内存字节数；失败时返回 0。
fn get_avail_bytes() -> u64 {
    let mut mem = MEMORYSTATUSEX {
        dwLength: std::mem::size_of::<MEMORYSTATUSEX>() as u32,
        ..Default::default()
    };
    unsafe {
        if GlobalMemoryStatusEx(&mut mem).is_ok() {
            mem.ullAvailPhys
        } else {
            0
        }
    }
}

type NtQuerySystemInformationFn = unsafe extern "system" fn(
    _system_information_class: i32,
    _system_information: *mut core::ffi::c_void,
    _system_information_length: u32,
    _return_length: *mut u32,
) -> i32;

fn nt_query_system_information() -> Option<NtQuerySystemInformationFn> {
    unsafe {
        let module = GetModuleHandleW(windows::core::w!("ntdll.dll")).ok()?;
        let proc = GetProcAddress(module, PCSTR(c"NtQuerySystemInformation".as_ptr().cast()))?;
        Some(std::mem::transmute::<
            unsafe extern "system" fn() -> isize,
            NtQuerySystemInformationFn,
        >(proc))
    }
}

/// 通过 NtQuerySystemInformation(SystemMemoryListInformation)
/// 查询准确的待机页和已修改页数量。
fn get_cached_bytes(page_size: u32) -> u64 {
    let nt_query = match nt_query_system_information() {
        Some(f) => f,
        None => return 0,
    };
    let mut info = MemoryListInformation::default();
    let mut returned = 0u32;
    unsafe {
        let status = nt_query(
            SYSTEM_MEMORY_LIST_INFORMATION_CLASS,
            &mut info as *mut _ as *mut _,
            std::mem::size_of::<MemoryListInformation>() as u32,
            &mut returned,
        );
        if status != 0 {
            return 0;
        }
    }
    let standby_pages: usize = info.page_count_by_priority.iter().sum();
    let modified_pages = info.modified_page_count;
    ((standby_pages + modified_pages) as u64) * (page_size as u64)
}

/// 从 PROCESSENTRY32W 复制出的条目，避免回调持有
/// 指向迭代缓冲区的悬垂引用。
struct ProcessEntry {
    pid: u32,
    name: String,
}

/// 遍历 toolhelp 快照中的所有进程，并对每个进程调用 `f`。
///
/// 即使 `f` 返回错误，快照句柄也会通过 [`OwnedHandle`] 自动关闭。
fn for_each_process<F>(mut f: F) -> PyResult<()>
where
    F: FnMut(ProcessEntry) -> PyResult<()>,
{
    unsafe {
        let snap = OwnedHandle(
            CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
                .map_err(|e| pyo3::exceptions::PyOSError::new_err(format!("snapshot: {e}")))?,
        );

        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };

        if Process32FirstW(snap.0, &mut entry).is_ok() {
            loop {
                let pid = entry.th32ProcessID;
                if pid != 0 {
                    let name = wide_to_string(&entry.szExeFile);
                    f(ProcessEntry { pid, name })?;
                }
                if Process32NextW(snap.0, &mut entry).is_err() {
                    break;
                }
            }
        }
    }
    Ok(())
}

// ----- memory_stats -------------------------------------------------------

#[pyfunction]
fn memory_stats(py: Python<'_>) -> PyResult<PyObject> {
    let mut mem = MEMORYSTATUSEX {
        dwLength: std::mem::size_of::<MEMORYSTATUSEX>() as u32,
        ..Default::default()
    };
    unsafe {
        GlobalMemoryStatusEx(&mut mem)
            .map_err(|e| pyo3::exceptions::PyOSError::new_err(format!("{e}")))?;
    }

    let total = mem.ullTotalPhys;
    let avail = mem.ullAvailPhys;
    let used = total.saturating_sub(avail);
    let percent = if total > 0 {
        (used as f64) / (total as f64) * 100.0
    } else {
        0.0
    };

    let mut perf = PERFORMANCE_INFORMATION::default();
    let cached_bytes = unsafe {
        if GetPerformanceInfo(&mut perf, std::mem::size_of::<PERFORMANCE_INFORMATION>() as u32)
            .is_ok()
        {
            get_cached_bytes(perf.PageSize as u32)
        } else {
            0
        }
    };

    let d = PyDict::new_bound(py);
    d.set_item("total", total)?;
    d.set_item("avail", avail)?;
    d.set_item("used", used)?;
    d.set_item("percent", percent)?;
    d.set_item("cached", cached_bytes)?;
    Ok(d.into())
}

// ----- process_list -------------------------------------------------------

/// NtQuerySystemInformation 不可用时，
/// 回退到 ToolHelp + 逐进程 OpenProcess。
fn process_list_fallback(py: Python<'_>) -> PyResult<PyObject> {
    let list = PyList::empty_bound(py);
    for_each_process(|entry| {
        let ws = working_set_for(entry.pid).unwrap_or(0);
        let item = PyDict::new_bound(py);
        item.set_item("pid", entry.pid)?;
        item.set_item("name", entry.name)?;
        item.set_item("working_set", ws)?;
        list.append(item)?;
        Ok(())
    })?;
    Ok(list.into())
}

#[pyfunction]
fn process_list(py: Python<'_>) -> PyResult<PyObject> {
    let nt_query = match nt_query_system_information() {
        Some(f) => f,
        None => return process_list_fallback(py),
    };

    let mut needed = 0u32;
    let status = unsafe {
        nt_query(
            SYSTEM_PROCESS_INFORMATION_CLASS,
            std::ptr::null_mut(),
            0,
            &mut needed,
        )
    };
    // 首次调用预期会返回 STATUS_INFO_LENGTH_MISMATCH (0xC0000004)。
    const STATUS_INFO_LENGTH_MISMATCH: i32 = 0xC0000004_u32 as i32;
    if status != STATUS_INFO_LENGTH_MISMATCH && status != 0 {
        return process_list_fallback(py);
    }

    let mut buffer = vec![0; needed as usize];
    let status = unsafe {
        nt_query(
            SYSTEM_PROCESS_INFORMATION_CLASS,
            buffer.as_mut_ptr() as *mut _,
            buffer.len() as u32,
            &mut needed,
        )
    };
    if status != 0 {
        return process_list_fallback(py);
    }

    let list = PyList::empty_bound(py);
    let mut offset = 0usize;
    unsafe {
        loop {
            let info = &*(buffer.as_ptr().add(offset) as *const SystemProcessInformation);
            let pid = info.unique_process_id as u32;
            if pid != 0 {
                let name = if !info.image_name.buffer.is_null() && info.image_name.length > 0 {
                    let slice = std::slice::from_raw_parts(
                        info.image_name.buffer,
                        info.image_name.length as usize / 2,
                    );
                    String::from_utf16_lossy(slice)
                } else if pid == 4 {
                    "System".to_string()
                } else {
                    String::new()
                };
                let ws = info.working_set_size as u64;
                let item = PyDict::new_bound(py);
                item.set_item("pid", pid)?;
                item.set_item("name", name)?;
                item.set_item("working_set", ws)?;
                list.append(item)?;
            }
            if info.next_entry_offset == 0 {
                break;
            }
            offset += info.next_entry_offset as usize;
        }
    }
    Ok(list.into())
}

// ----- trim_process / trim_all -------------------------------------------

fn working_set_for(pid: u32) -> Option<u64> {
    unsafe {
        let h = OwnedHandle(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?);
        working_set_for_handle(h.0)
    }
}

fn working_set_for_handle(h: HANDLE) -> Option<u64> {
    let mut counters = PROCESS_MEMORY_COUNTERS::default();
    let size = std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
    unsafe {
        if GetProcessMemoryInfo(h, &mut counters, size).is_ok() {
            Some(counters.WorkingSetSize as u64)
        } else {
            None
        }
    }
}

fn trim_pid(pid: u32) -> bool {
    unsafe {
        let h = match OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
        {
            Ok(h) => OwnedHandle(h),
            Err(_) => return false,
        };
        EmptyWorkingSet(h.0).is_ok()
    }
}

#[pyfunction]
fn trim_process(pid: u32) -> bool {
    trim_pid(pid)
}

/// 清理所有进程并报告实际释放的内存。
///
/// 不直接累加各进程 working set（那会高估释放量），
/// 而是在清理前后测量全局可用内存，从而得到真实释放量。
#[pyfunction]
fn trim_all(py: Python<'_>) -> PyResult<PyObject> {
    trim_all_filtered(py, false, "", "balanced", false)
}

#[pyfunction]
fn trim_all_filtered(
    py: Python<'_>,
    exclude_foreground_process: bool,
    excluded_process_names: &str,
    cleaning_mode: &str,
    clear_standby_too: bool,
) -> PyResult<PyObject> {
    let avail_before = get_avail_bytes();
    let mut trimmed_count: u64 = 0;
    let mut protected_skipped: u64 = 0;
    let mut reports: Vec<(u32, String, u64, u64, u64, bool)> = Vec::new();
    let current_pid = unsafe { GetCurrentProcessId() };
    let foreground_pid = if exclude_foreground_process {
        foreground_pid()
    } else {
        None
    };
    let excluded_names = parse_excluded_names_cached(excluded_process_names);
    let mode = match cleaning_mode {
        "conservative" | "balanced" | "aggressive" => cleaning_mode,
        _ => "balanced",
    };
    let protected_names = protected_names_for_mode(mode);
    let min_ws = cleaning_mode_min_ws(mode);

    // 先收集候选项，确保每个进程只打开一次。
    let mut candidates: Vec<(u32, String)> = Vec::new();
    for_each_process(|entry| {
        if entry.pid == current_pid || foreground_pid == Some(entry.pid) {
            return Ok(());
        }
        let name = normalize_process_name(&entry.name);
        if excluded_names.contains(&name) {
            return Ok(());
        }
        if protected_names.contains(name.as_str()) {
            protected_skipped += 1;
            return Ok(());
        }
        candidates.push((entry.pid, entry.name));
        Ok(())
    })?;

    // 使用单个 OpenProcess 句柄清理每个候选进程。
    for (pid, display_name) in candidates {
        unsafe {
            let h = match OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION,
                false,
                pid,
            ) {
                Ok(h) => OwnedHandle(h),
                Err(_) => continue,
            };
            let before_ws = match working_set_for_handle(h.0) {
                Some(ws) => ws,
                None => continue,
            };
            if before_ws < min_ws {
                continue;
            }
            let success = EmptyWorkingSet(h.0).is_ok();
            let after_ws = if success {
                working_set_for_handle(h.0).unwrap_or(before_ws)
            } else {
                before_ws
            };
            let freed = before_ws.saturating_sub(after_ws);
            if success {
                trimmed_count += 1;
            }
            reports.push((pid, display_name, before_ws, after_ws, freed, success));
        }
    }

    let system_report = apply_system_cleaning(mode, clear_standby_too);
    let avail_after = get_avail_bytes();
    let freed = avail_after.saturating_sub(avail_before);
    let process_freed: u64 = reports.iter().map(|r| r.4).sum();
    let system_freed = system_report.total_freed();
    let process_items = PyList::empty_bound(py);
    for (pid, name, before_ws, after_ws, freed_ws, success) in &reports {
        let item = PyDict::new_bound(py);
        item.set_item("pid", *pid)?;
        item.set_item("name", name)?;
        item.set_item("before_ws", *before_ws)?;
        item.set_item("after_ws", *after_ws)?;
        item.set_item("freed_bytes", *freed_ws)?;
        item.set_item("freed_mb", (*freed_ws as f64) / (1024.0 * 1024.0))?;
        item.set_item("success", *success)?;
        process_items.append(item)?;
    }

    reports.sort_by_key(|r| std::cmp::Reverse(r.4));
    let top_freed = PyList::empty_bound(py);
    for (pid, name, before_ws, after_ws, freed_ws, success) in reports.iter().take(10) {
        let item = PyDict::new_bound(py);
        item.set_item("pid", *pid)?;
        item.set_item("name", name)?;
        item.set_item("before_ws", *before_ws)?;
        item.set_item("after_ws", *after_ws)?;
        item.set_item("freed_bytes", *freed_ws)?;
        item.set_item("freed_mb", (*freed_ws as f64) / (1024.0 * 1024.0))?;
        item.set_item("success", *success)?;
        top_freed.append(item)?;
    }

    let d = PyDict::new_bound(py);
    d.set_item("trimmed", trimmed_count)?;
    d.set_item("freed_bytes", freed)?;
    d.set_item("freed_mb", (freed as f64) / (1024.0 * 1024.0))?;
    d.set_item("process_freed_bytes", process_freed)?;
    d.set_item("process_freed_mb", (process_freed as f64) / (1024.0 * 1024.0))?;
    d.set_item("system_freed_bytes", system_freed)?;
    d.set_item("system_freed_mb", (system_freed as f64) / (1024.0 * 1024.0))?;
    d.set_item("protected_skipped", protected_skipped)?;
    d.set_item("processes", process_items)?;
    d.set_item("top_freed", top_freed)?;
    d.set_item("cleaning_mode", mode)?;
    d.set_item("min_working_set_bytes", min_ws)?;
    d.set_item(
        "system_working_set_cleared",
        system_report.system_working_set_cleared,
    )?;
    d.set_item(
        "system_working_set_freed_bytes",
        system_report.system_working_set_freed_bytes,
    )?;
    d.set_item(
        "system_working_set_freed_mb",
        (system_report.system_working_set_freed_bytes as f64) / (1024.0 * 1024.0),
    )?;
    d.set_item("system_cache_cleared", system_report.system_cache_cleared)?;
    d.set_item(
        "system_cache_freed_bytes",
        system_report.system_cache_freed_bytes,
    )?;
    d.set_item(
        "system_cache_freed_mb",
        (system_report.system_cache_freed_bytes as f64) / (1024.0 * 1024.0),
    )?;
    d.set_item(
        "modified_page_list_cleared",
        system_report.modified_page_list_cleared,
    )?;
    d.set_item(
        "modified_page_list_freed_bytes",
        system_report.modified_page_list_freed_bytes,
    )?;
    d.set_item(
        "modified_page_list_freed_mb",
        (system_report.modified_page_list_freed_bytes as f64) / (1024.0 * 1024.0),
    )?;
    d.set_item("standby_cleared", system_report.standby_cleared)?;
    d.set_item("standby_freed_bytes", system_report.standby_freed_bytes)?;
    d.set_item(
        "standby_freed_mb",
        (system_report.standby_freed_bytes as f64) / (1024.0 * 1024.0),
    )?;
    Ok(d.into())
}

// ----- enable_privilege --------------------------------------------------

fn enable_priv_internal(name: &str) -> bool {
    unsafe {
        let mut token = HANDLE::default();
        let proc = GetCurrentProcess();
        if OpenProcessToken(proc, TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &mut token).is_err() {
            return false;
        }
        let token = OwnedHandle(token);

        let mut wide: Vec<u16> = name.encode_utf16().collect();
        wide.push(0);
        let mut luid = LUID::default();
        if LookupPrivilegeValueW(
            windows::core::PCWSTR::null(),
            windows::core::PCWSTR(wide.as_ptr()),
            &mut luid,
        )
        .is_err()
        {
            return false;
        }

        let tp = TOKEN_PRIVILEGES {
            PrivilegeCount: 1,
            Privileges: [LUID_AND_ATTRIBUTES {
                Luid: luid,
                Attributes: SE_PRIVILEGE_ENABLED,
            }],
        };
        AdjustTokenPrivileges(token.0, false, Some(&tp), 0, None, None).is_ok()
    }
}

#[pyfunction]
fn enable_privilege(name: &str) -> bool {
    enable_priv_internal(name)
}

// ----- system cleanup -----------------------------------------------------

#[allow(non_snake_case)]
type NtSetSystemInformationFn = unsafe extern "system" fn(
    SystemInformationClass: i32,
    SystemInformation: *mut core::ffi::c_void,
    SystemInformationLength: u32,
) -> i32;

fn nt_set_system_information() -> Option<NtSetSystemInformationFn> {
    unsafe {
        let module = GetModuleHandleW(windows::core::w!("ntdll.dll")).ok()?;
        let proc = GetProcAddress(module, PCSTR(c"NtSetSystemInformation".as_ptr().cast()))?;
        Some(std::mem::transmute::<
            unsafe extern "system" fn() -> isize,
            NtSetSystemInformationFn,
        >(proc))
    }
}

#[derive(Default)]
struct SystemCleanReport {
    system_working_set_cleared: bool,
    system_working_set_freed_bytes: u64,
    system_cache_cleared: bool,
    system_cache_freed_bytes: u64,
    modified_page_list_cleared: bool,
    modified_page_list_freed_bytes: u64,
    standby_cleared: bool,
    standby_freed_bytes: u64,
}

impl SystemCleanReport {
    fn total_freed(&self) -> u64 {
        self.system_working_set_freed_bytes
            .saturating_add(self.system_cache_freed_bytes)
            .saturating_add(self.modified_page_list_freed_bytes)
            .saturating_add(self.standby_freed_bytes)
    }
}

fn measure_avail_delta(action: impl FnOnce() -> bool) -> (bool, u64) {
    let before = get_avail_bytes();
    let ok = action();
    if !ok {
        return (false, 0);
    }
    let after = get_avail_bytes();
    (true, after.saturating_sub(before))
}

fn issue_memory_list_command(command: u32) -> bool {
    let _ = enable_priv_internal("SeProfileSingleProcessPrivilege");
    let f = match nt_set_system_information() {
        Some(f) => f,
        None => return false,
    };
    let mut command = command;
    let status = unsafe {
        f(
            SYSTEM_MEMORY_LIST_INFORMATION_CLASS,
            &mut command as *mut u32 as *mut _,
            std::mem::size_of::<u32>() as u32,
        )
    };
    status == 0
}

fn clear_system_working_sets() -> bool {
    issue_memory_list_command(MEMORY_EMPTY_WORKING_SETS)
}

fn clear_system_file_cache() -> bool {
    let _ = enable_priv_internal("SeIncreaseQuotaPrivilege");
    let f = match nt_set_system_information() {
        Some(f) => f,
        None => return false,
    };
    let mut info = SystemFileCacheInformation {
        minimum_working_set: usize::MAX,
        maximum_working_set: usize::MAX,
        ..Default::default()
    };
    let status = unsafe {
        f(
            SYSTEM_FILE_CACHE_INFORMATION_EX_CLASS,
            &mut info as *mut SystemFileCacheInformation as *mut _,
            std::mem::size_of::<SystemFileCacheInformation>() as u32,
        )
    };
    status == 0
}

fn flush_modified_page_list() -> bool {
    issue_memory_list_command(MEMORY_FLUSH_MODIFIED_LIST)
}

fn apply_system_cleaning(mode: &str, clear_standby_too: bool) -> SystemCleanReport {
    let mut report = SystemCleanReport::default();

    if mode != "conservative" {
        (report.system_working_set_cleared, report.system_working_set_freed_bytes) =
            measure_avail_delta(clear_system_working_sets);
        (report.system_cache_cleared, report.system_cache_freed_bytes) =
            measure_avail_delta(clear_system_file_cache);
    }

    if mode == "aggressive" {
        (report.modified_page_list_cleared, report.modified_page_list_freed_bytes) =
            measure_avail_delta(flush_modified_page_list);
    }

    if clear_standby_too || mode == "aggressive" {
        (report.standby_cleared, report.standby_freed_bytes) =
            measure_avail_delta(|| issue_memory_list_command(MEMORY_PURGE_STANDBY_LIST));
    }

    report
}

#[pyfunction]
fn clear_standby() -> bool {
    issue_memory_list_command(MEMORY_PURGE_STANDBY_LIST)
}

// ----- 模块初始化 ---------------------------------------------------------

#[pymodule]
fn _core(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // 导入时尽力启用相关权限。
    let _ = enable_priv_internal("SeIncreaseQuotaPrivilege");
    let _ = enable_priv_internal("SeProfileSingleProcessPrivilege");
    let _ = enable_priv_internal("SeDebugPrivilege");

    m.add_function(wrap_pyfunction!(memory_stats, m)?)?;
    m.add_function(wrap_pyfunction!(process_list, m)?)?;
    m.add_function(wrap_pyfunction!(trim_process, m)?)?;
    m.add_function(wrap_pyfunction!(trim_all, m)?)?;
    m.add_function(wrap_pyfunction!(trim_all_filtered, m)?)?;
    m.add_function(wrap_pyfunction!(clear_standby, m)?)?;
    m.add_function(wrap_pyfunction!(enable_privilege, m)?)?;
    Ok(())
}
