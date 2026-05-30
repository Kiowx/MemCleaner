use std::mem::size_of;

use windows::core::PCSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE, LUID};
use windows::Win32::Security::{
    AdjustTokenPrivileges, LookupPrivilegeValueW, LUID_AND_ATTRIBUTES, SE_PRIVILEGE_ENABLED,
    TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES, TOKEN_QUERY,
};
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::Win32::System::ProcessStatus::{
    EmptyWorkingSet, GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS,
};
use windows::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
use windows::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, OpenProcessToken, PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESS_SET_QUOTA,
};

const SYSTEM_MEMORY_LIST_INFORMATION_CLASS: i32 = 80;
const SYSTEM_FILE_CACHE_INFORMATION_EX_CLASS: i32 = 81;
const MEMORY_EMPTY_WORKING_SETS: u32 = 2;
const MEMORY_FLUSH_MODIFIED_LIST: u32 = 3;
const MEMORY_PURGE_STANDBY_LIST: u32 = 4;

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

#[derive(Default)]
pub struct SystemCleanReport {
    pub system_working_set_cleared: bool,
    pub system_working_set_freed_bytes: u64,
    pub system_cache_cleared: bool,
    pub system_cache_freed_bytes: u64,
    pub modified_page_list_cleared: bool,
    pub modified_page_list_freed_bytes: u64,
    pub standby_cleared: bool,
    pub standby_freed_bytes: u64,
}

impl SystemCleanReport {
    pub fn total_freed(&self) -> u64 {
        self.system_working_set_freed_bytes
            .saturating_add(self.system_cache_freed_bytes)
            .saturating_add(self.modified_page_list_freed_bytes)
            .saturating_add(self.standby_freed_bytes)
    }
}

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

pub fn avail_bytes() -> u64 {
    let mut mem = MEMORYSTATUSEX {
        dwLength: size_of::<MEMORYSTATUSEX>() as u32,
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

pub fn enable_privilege(name: &str) -> bool {
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

pub fn working_set_for(pid: u32) -> Option<u64> {
    unsafe {
        let handle = OwnedHandle(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?);
        working_set_for_handle(handle.0)
    }
}

fn working_set_for_handle(handle: HANDLE) -> Option<u64> {
    let mut counters = PROCESS_MEMORY_COUNTERS::default();
    let size = size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
    unsafe {
        if GetProcessMemoryInfo(handle, &mut counters, size).is_ok() {
            Some(counters.WorkingSetSize as u64)
        } else {
            None
        }
    }
}

#[allow(dead_code)]
pub fn trim_pid(pid: u32) -> bool {
    trim_process_report(pid)
        .map(|report| report.success)
        .unwrap_or(false)
}

#[allow(dead_code)]
pub struct ProcessTrimReport {
    pub before_ws: u64,
    pub after_ws: u64,
    pub freed_ws: u64,
    pub success: bool,
}

pub fn trim_process_report(pid: u32) -> Option<ProcessTrimReport> {
    unsafe {
        let handle = OwnedHandle(
            OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION,
                false,
                pid,
            )
            .ok()?,
        );
        let before_ws = working_set_for_handle(handle.0)?;
        let success = EmptyWorkingSet(handle.0).is_ok();
        let after_ws = if success {
            working_set_for_handle(handle.0).unwrap_or(before_ws)
        } else {
            before_ws
        };
        Some(ProcessTrimReport {
            before_ws,
            after_ws,
            freed_ws: before_ws.saturating_sub(after_ws),
            success,
        })
    }
}

fn measure_avail_delta(action: impl FnOnce() -> bool) -> (bool, u64) {
    let before = avail_bytes();
    let ok = action();
    if !ok {
        return (false, 0);
    }
    let after = avail_bytes();
    (true, after.saturating_sub(before))
}

fn issue_memory_list_command(command: u32) -> bool {
    let _ = enable_privilege("SeProfileSingleProcessPrivilege");
    let Some(f) = nt_set_system_information() else {
        return false;
    };
    let mut command = command;
    let status = unsafe {
        f(
            SYSTEM_MEMORY_LIST_INFORMATION_CLASS,
            &mut command as *mut u32 as *mut _,
            size_of::<u32>() as u32,
        )
    };
    status == 0
}

fn clear_system_working_sets() -> bool {
    issue_memory_list_command(MEMORY_EMPTY_WORKING_SETS)
}

fn clear_system_file_cache() -> bool {
    let _ = enable_privilege("SeIncreaseQuotaPrivilege");
    let Some(f) = nt_set_system_information() else {
        return false;
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
            size_of::<SystemFileCacheInformation>() as u32,
        )
    };
    status == 0
}

fn flush_modified_page_list() -> bool {
    issue_memory_list_command(MEMORY_FLUSH_MODIFIED_LIST)
}

pub fn clear_standby() -> bool {
    issue_memory_list_command(MEMORY_PURGE_STANDBY_LIST)
}

pub fn apply_system_cleaning(mode: &str, clear_standby_too: bool) -> SystemCleanReport {
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
            measure_avail_delta(clear_standby);
    }

    report
}
