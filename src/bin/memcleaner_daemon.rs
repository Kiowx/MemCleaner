#![cfg_attr(windows, windows_subsystem = "windows")]

use std::env;
use std::ffi::OsStr;
use std::fs;
use std::collections::HashSet;
use std::mem::size_of;
use std::io::Write;
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use serde::Deserialize;
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{CloseHandle, GetLastError, HANDLE, HWND, LPARAM, LRESULT, LUID, POINT, WPARAM};
use windows::Win32::Security::{
    AdjustTokenPrivileges, LookupPrivilegeValueW, LUID_AND_ATTRIBUTES, SE_PRIVILEGE_ENABLED,
    TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES, TOKEN_QUERY,
};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::Win32::System::ProcessStatus::{
    EmptyWorkingSet, GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS,
};
use windows::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
use windows::Win32::System::Threading::{
    CreateMutexW, GetCurrentProcess, GetCurrentProcessId, OpenProcess, OpenProcessToken,
    PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SET_QUOTA,
};
use windows::Win32::UI::Shell::{
    IsUserAnAdmin, Shell_NotifyIconW, ShellExecuteW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE,
    NIM_MODIFY,
    NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DispatchMessageW,
    FindWindowW, GetCursorPos, GetMessageW, LoadIconW, LoadImageW, PostMessageW, PostQuitMessage, RegisterClassW,
    SetForegroundWindow, TrackPopupMenu, TranslateMessage, IDI_APPLICATION, SW_SHOWNORMAL,
    CS_DBLCLKS, HICON, IMAGE_ICON, LR_DEFAULTSIZE, LR_LOADFROMFILE, MF_SEPARATOR, MF_STRING, MSG,
    TPM_BOTTOMALIGN, TPM_LEFTALIGN, WINDOW_EX_STYLE, WINDOW_STYLE, WM_APP, WM_COMMAND,
    WM_DESTROY, WM_LBUTTONDBLCLK, WM_MOUSEMOVE, WM_RBUTTONUP, WNDCLASSW,
    GetForegroundWindow, GetWindowThreadProcessId,
};

#[path = "../cleaning.rs"]
mod cleaning;

const WM_TRAY: u32 = WM_APP + 1;
const ID_SHOW: usize = 1001;
const ID_CLEAN: usize = 1002;
const ID_QUIT: usize = 1003;
const TRAY_UID: u32 = 1;
const LOG_MAX_BYTES: u64 = 512 * 1024;
const LOW_YIELD_FREED_BYTES: u64 = 64 * 1024 * 1024;
const LOW_YIELD_COOLDOWN_MULTIPLIER: u64 = 3;
const SYSTEM_MEMORY_LIST_INFORMATION_CLASS: i32 = 80;
const SYSTEM_FILE_CACHE_INFORMATION_EX_CLASS: i32 = 81;
const MEMORY_EMPTY_WORKING_SETS: u32 = 2;
const MEMORY_FLUSH_MODIFIED_LIST: u32 = 3;
const MEMORY_PURGE_STANDBY_LIST: u32 = 4;

static RUNNING: AtomicBool = AtomicBool::new(true);

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

#[derive(Clone, Deserialize)]
#[serde(default)]
struct Config {
    threshold_enabled: bool,
    threshold_percent: u64,
    threshold_trigger_seconds: u64,
    threshold_cooldown_seconds: u64,
    interval_enabled: bool,
    interval_minutes: u64,
    auto_elevate: bool,
    clear_standby_too: bool,
    cleaning_mode: String,
    exclude_foreground_process: bool,
    excluded_process_names: String,
    language: String,
    gui_exe_path: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            threshold_enabled: false,
            threshold_percent: 80,
            threshold_trigger_seconds: 5,
            threshold_cooldown_seconds: 60,
            interval_enabled: false,
            interval_minutes: 30,
            auto_elevate: false,
            clear_standby_too: false,
            cleaning_mode: "balanced".to_string(),
            exclude_foreground_process: true,
            excluded_process_names: String::new(),
            language: "zh".to_string(),
            gui_exe_path: String::new(),
        }
    }
}

impl Config {
    fn sanitize(&mut self) {
        self.threshold_percent = self.threshold_percent.clamp(1, 99);
        self.threshold_trigger_seconds = self.threshold_trigger_seconds.min(300);
        self.threshold_cooldown_seconds = self.threshold_cooldown_seconds.clamp(5, 3600);
        self.interval_minutes = self.interval_minutes.clamp(1, 1440);
        if !matches!(
            self.cleaning_mode.as_str(),
            "conservative" | "balanced" | "aggressive"
        ) {
            self.cleaning_mode = "balanced".to_string();
        }
        if self.cleaning_mode == "aggressive" {
            self.clear_standby_too = true;
        }
        if !self.language.eq_ignore_ascii_case("en") {
            self.language = "zh".to_string();
        }
    }
}

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

fn wide(s: &str) -> Vec<u16> {
    OsStr::new(s).encode_wide().chain([0]).collect()
}

fn wide_to_string(buf: &[u16]) -> String {
    let len = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    String::from_utf16_lossy(&buf[..len])
}

fn config_path() -> PathBuf {
    let base = env::var_os("APPDATA")
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("memcleaner").join("config.json")
}

fn log_path() -> PathBuf {
    config_path()
        .parent()
        .map(|p| p.join("daemon.log"))
        .unwrap_or_else(|| PathBuf::from("daemon.log"))
}

fn log_event(message: &str) {
    let path = log_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if path
        .metadata()
        .map(|m| m.len() > LOG_MAX_BYTES)
        .unwrap_or(false)
    {
        let rotated = path.with_extension("log.1");
        let _ = fs::remove_file(&rotated);
        let _ = fs::rename(&path, rotated);
    }
    let now = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{now} {message}");
    }
}

fn load_config() -> Config {
    let path = config_path();
    let Ok(raw) = fs::read_to_string(path) else {
        return Config::default();
    };
    let mut cfg = serde_json::from_str::<Config>(&raw).unwrap_or_default();
    cfg.sanitize();
    cfg
}

fn load_config_cached(cache: &mut Option<(SystemTime, Config)>) -> Config {
    let path = config_path();
    let modified = path.metadata().and_then(|m| m.modified()).ok();
    if let (Some(modified), Some((cached_modified, cached_cfg))) = (modified, cache.as_ref()) {
        if *cached_modified == modified {
            return cached_cfg.clone();
        }
    }
    let cfg = load_config();
    if let Some(modified) = modified {
        *cache = Some((modified, cfg.clone()));
    }
    cfg
}

fn current_memory_usage() -> Option<(u64, u64)> {
    let mut mem = MEMORYSTATUSEX {
        dwLength: size_of::<MEMORYSTATUSEX>() as u32,
        ..Default::default()
    };
    unsafe {
        GlobalMemoryStatusEx(&mut mem).ok()?;
    }
    if mem.ullTotalPhys == 0 {
        return None;
    }
    let used = mem.ullTotalPhys.saturating_sub(mem.ullAvailPhys);
    Some((used, mem.ullTotalPhys))
}

fn memory_pressure_high(percent: f64, avail: u64, total: u64, threshold: u64) -> bool {
    cleaning::memory_pressure_high(percent, avail, total, threshold)
}

fn enable_privilege(name: &str) -> bool {
    unsafe {
        let mut token = HANDLE::default();
        if OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            &mut token,
        )
        .is_err()
        {
            return false;
        }
        let token = OwnedHandle(token);
        let mut wide_name: Vec<u16> = name.encode_utf16().collect();
        wide_name.push(0);
        let mut luid = LUID::default();
        if LookupPrivilegeValueW(PCWSTR::null(), PCWSTR(wide_name.as_ptr()), &mut luid).is_err() {
            return false;
        }
        let privileges = TOKEN_PRIVILEGES {
            PrivilegeCount: 1,
            Privileges: [LUID_AND_ATTRIBUTES {
                Luid: luid,
                Attributes: SE_PRIVILEGE_ENABLED,
            }],
        };
        AdjustTokenPrivileges(token.0, false, Some(&privileges), 0, None, None).is_ok()
    }
}

fn trim_pid(pid: u32) -> bool {
    unsafe {
        let Ok(handle) = OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return false;
        };
        let handle = OwnedHandle(handle);
        EmptyWorkingSet(handle.0).is_ok()
    }
}

fn working_set_for(pid: u32) -> Option<u64> {
    unsafe {
        let handle = OwnedHandle(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?);
        let mut counters = PROCESS_MEMORY_COUNTERS::default();
        let size = size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
        if GetProcessMemoryInfo(handle.0, &mut counters, size).is_ok() {
            Some(counters.WorkingSetSize as u64)
        } else {
            None
        }
    }
}

fn normalize_process_name(name: &str) -> String {
    cleaning::normalize_process_name(name)
}

fn parse_excluded_names(raw: &str) -> HashSet<String> {
    cleaning::parse_excluded_names(raw)
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

struct CleanSummary {
    trimmed: u64,
    protected_skipped: u64,
    process_freed: u64,
    top_freed: Vec<(String, u64)>,
}

#[derive(Default)]
struct SystemCleanSummary {
    system_working_set_cleared: bool,
    system_working_set_freed_bytes: u64,
    system_cache_cleared: bool,
    system_cache_freed_bytes: u64,
    modified_page_list_cleared: bool,
    modified_page_list_freed_bytes: u64,
    standby_cleared: bool,
    standby_freed_bytes: u64,
}

impl SystemCleanSummary {
    fn total_freed(&self) -> u64 {
        self.system_working_set_freed_bytes
            .saturating_add(self.system_cache_freed_bytes)
            .saturating_add(self.modified_page_list_freed_bytes)
            .saturating_add(self.standby_freed_bytes)
    }
}

fn trim_all(cfg: &Config) -> CleanSummary {
    let mut summary = CleanSummary {
        trimmed: 0,
        protected_skipped: 0,
        process_freed: 0,
        top_freed: Vec::new(),
    };
    let current_pid = unsafe { GetCurrentProcessId() };
    let foreground_pid = if cfg.exclude_foreground_process {
        foreground_pid()
    } else {
        None
    };
    let excluded_names = parse_excluded_names(&cfg.excluded_process_names);
    let protected_names = protected_names_for_mode(&cfg.cleaning_mode);
    let min_ws = cleaning_mode_min_ws(&cfg.cleaning_mode);
    unsafe {
        let Ok(snapshot) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
            return summary;
        };
        let snapshot = OwnedHandle(snapshot);
        let mut entry = PROCESSENTRY32W {
            dwSize: size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        if Process32FirstW(snapshot.0, &mut entry).is_ok() {
            loop {
                let pid = entry.th32ProcessID;
                let display_name = wide_to_string(&entry.szExeFile);
                let name = normalize_process_name(&display_name);
                if pid != 0
                    && pid != current_pid
                    && foreground_pid != Some(pid)
                    && !excluded_names.contains(&name)
                {
                    if protected_names.contains(name.as_str()) {
                        summary.protected_skipped += 1;
                    } else {
                        let before_ws = working_set_for(pid).unwrap_or(0);
                        if before_ws >= min_ws && trim_pid(pid) {
                            let after_ws = working_set_for(pid).unwrap_or(before_ws);
                            let freed = before_ws.saturating_sub(after_ws);
                            summary.trimmed += 1;
                            summary.process_freed = summary.process_freed.saturating_add(freed);
                            if freed > 0 {
                                summary.top_freed.push((display_name, freed));
                            }
                        }
                    }
                }
                if Process32NextW(snapshot.0, &mut entry).is_err() {
                    break;
                }
            }
        }
    }
    summary.top_freed.sort_by_key(|(_, freed)| std::cmp::Reverse(*freed));
    summary.top_freed.truncate(5);
    summary
}

#[allow(non_snake_case)]
type NtSetSystemInformationFn = unsafe extern "system" fn(
    SystemInformationClass: i32,
    SystemInformation: *mut core::ffi::c_void,
    SystemInformationLength: u32,
) -> i32;

fn nt_set_system_information() -> Option<NtSetSystemInformationFn> {
    unsafe {
        let module = GetModuleHandleW(w!("ntdll.dll")).ok()?;
        let proc = GetProcAddress(module, windows::core::PCSTR(b"NtSetSystemInformation\0".as_ptr()))?;
        Some(std::mem::transmute(proc))
    }
}

fn current_avail_bytes() -> u64 {
    current_memory_usage()
        .map(|(used, total)| total.saturating_sub(used))
        .unwrap_or(0)
}

fn measure_avail_delta(action: impl FnOnce() -> bool) -> (bool, u64) {
    let before = current_avail_bytes();
    let ok = action();
    if !ok {
        return (false, 0);
    }
    let after = current_avail_bytes();
    (true, after.saturating_sub(before))
}

fn issue_memory_list_command(command: u32) -> bool {
    let _ = enable_privilege("SeProfileSingleProcessPrivilege");
    unsafe {
        let Some(f) = nt_set_system_information() else {
            return false;
        };
        let mut command = command;
        f(
            SYSTEM_MEMORY_LIST_INFORMATION_CLASS,
            &mut command as *mut u32 as *mut _,
            size_of::<u32>() as u32,
        ) == 0
    }
}

fn clear_system_working_sets() -> bool {
    issue_memory_list_command(MEMORY_EMPTY_WORKING_SETS)
}

fn clear_system_file_cache() -> bool {
    let _ = enable_privilege("SeIncreaseQuotaPrivilege");
    unsafe {
        let Some(f) = nt_set_system_information() else {
            return false;
        };
        let mut info = SystemFileCacheInformation {
            minimum_working_set: usize::MAX,
            maximum_working_set: usize::MAX,
            ..Default::default()
        };
        f(
            SYSTEM_FILE_CACHE_INFORMATION_EX_CLASS,
            &mut info as *mut SystemFileCacheInformation as *mut _,
            size_of::<SystemFileCacheInformation>() as u32,
        ) == 0
    }
}

fn flush_modified_page_list() -> bool {
    issue_memory_list_command(MEMORY_FLUSH_MODIFIED_LIST)
}

fn clear_standby() -> bool {
    issue_memory_list_command(MEMORY_PURGE_STANDBY_LIST)
}

fn apply_system_cleaning(cfg: &Config) -> SystemCleanSummary {
    let mut summary = SystemCleanSummary::default();

    if cfg.cleaning_mode != "conservative" {
        (summary.system_working_set_cleared, summary.system_working_set_freed_bytes) =
            measure_avail_delta(clear_system_working_sets);
        (summary.system_cache_cleared, summary.system_cache_freed_bytes) =
            measure_avail_delta(clear_system_file_cache);
    }

    if cfg.cleaning_mode == "aggressive" {
        (summary.modified_page_list_cleared, summary.modified_page_list_freed_bytes) =
            measure_avail_delta(flush_modified_page_list);
    }

    if cfg.clear_standby_too || cfg.cleaning_mode == "aggressive" {
        (summary.standby_cleared, summary.standby_freed_bytes) =
            measure_avail_delta(clear_standby);
    }

    summary
}

struct CleanOutcome {
    global_freed: u64,
}

fn clean_now(trigger: &str) -> CleanOutcome {
    let cfg = load_config();
    let avail_before = current_avail_bytes();
    let summary = trim_all(&cfg);
    let top = summary
        .top_freed
        .iter()
        .map(|(name, freed)| format!("{name}:{:.0}MB", (*freed as f64) / (1024.0 * 1024.0)))
        .collect::<Vec<_>>()
        .join(",");
    let system_summary = apply_system_cleaning(&cfg);
    let global_freed = current_avail_bytes().saturating_sub(avail_before);
    let system_freed = system_summary.total_freed();
    log_event(&format!(
        "clean trigger={trigger} mode={} trimmed={} global_freed_mb={:.0} process_freed_mb={:.0} system_freed_mb={:.0} system_ws_freed_mb={:.0} file_cache_freed_mb={:.0} modified_freed_mb={:.0} standby_freed_mb={:.0} protected_skipped={} system_ws_cleared={} file_cache_cleared={} modified_cleared={} standby_cleared={} top=[{top}]",
        cfg.cleaning_mode,
        summary.trimmed,
        (global_freed as f64) / (1024.0 * 1024.0),
        (summary.process_freed as f64) / (1024.0 * 1024.0),
        (system_freed as f64) / (1024.0 * 1024.0),
        (system_summary.system_working_set_freed_bytes as f64) / (1024.0 * 1024.0),
        (system_summary.system_cache_freed_bytes as f64) / (1024.0 * 1024.0),
        (system_summary.modified_page_list_freed_bytes as f64) / (1024.0 * 1024.0),
        (system_summary.standby_freed_bytes as f64) / (1024.0 * 1024.0),
        summary.protected_skipped,
        system_summary.system_working_set_cleared,
        system_summary.system_cache_cleared,
        system_summary.modified_page_list_cleared,
        system_summary.standby_cleared,
    ));
    CleanOutcome {
        global_freed,
    }
}

fn scheduler_loop() {
    let mut last_threshold: Option<Instant> = None;
    let mut last_interval = Instant::now();
    let mut last_threshold_value = 0_u64;
    let mut last_trigger_seconds = u64::MAX;
    let mut threshold_high_since: Option<Instant> = None;
    let mut low_yield_until: Option<Instant> = None;
    let mut config_cache: Option<(SystemTime, Config)> = None;
    let mut last_skip_log = Instant::now() - Duration::from_secs(60);
    while RUNNING.load(Ordering::Relaxed) {
        let cfg = load_config_cached(&mut config_cache);
        let now = Instant::now();
        if cfg.threshold_percent != last_threshold_value
            || cfg.threshold_trigger_seconds != last_trigger_seconds
        {
            threshold_high_since = None;
            low_yield_until = None;
            last_threshold_value = cfg.threshold_percent;
            last_trigger_seconds = cfg.threshold_trigger_seconds;
        }
        if cfg.threshold_enabled {
            if let Some((used, total)) = current_memory_usage() {
                let percent = ((used as f64) / (total as f64) * 100.0).min(100.0);
                let avail = total.saturating_sub(used);
                let cooldown = Duration::from_secs(cfg.threshold_cooldown_seconds.max(5));
                let pressure_high =
                    memory_pressure_high(percent, avail, total, cfg.threshold_percent);
                if pressure_high {
                    if threshold_high_since.is_none() {
                        threshold_high_since = Some(now);
                    }
                } else {
                    threshold_high_since = None;
                }
                let trigger_wait = Duration::from_secs(cfg.threshold_trigger_seconds);
                let threshold_held = threshold_high_since
                    .map(|since| now.duration_since(since) >= trigger_wait)
                    .unwrap_or(false);
                if now.duration_since(last_skip_log) >= Duration::from_secs(60) {
                    if !pressure_high {
                        log_event(&format!(
                            "threshold idle reason=below_pressure percent={percent:.1} threshold={} avail_mb={:.0}",
                            cfg.threshold_percent,
                            (avail as f64) / (1024.0 * 1024.0)
                        ));
                    } else if !threshold_held {
                        let held = threshold_high_since
                            .map(|since| now.duration_since(since).as_secs())
                            .unwrap_or(0);
                        log_event(&format!(
                            "threshold idle reason=waiting_trigger held_seconds={held} trigger_seconds={}",
                            cfg.threshold_trigger_seconds
                        ));
                    } else if low_yield_until.map(|until| now < until).unwrap_or(false) {
                        let remaining = low_yield_until
                            .map(|until| until.saturating_duration_since(now).as_secs())
                            .unwrap_or(0);
                        log_event(&format!(
                            "threshold idle reason=low_yield_pause remaining_seconds={remaining}"
                        ));
                    } else if last_threshold
                        .map(|last| now.duration_since(last) < cooldown)
                        .unwrap_or(false)
                    {
                        let remaining = last_threshold
                            .map(|last| cooldown.saturating_sub(now.duration_since(last)).as_secs())
                            .unwrap_or(0);
                        log_event(&format!(
                            "threshold idle reason=cooldown remaining_seconds={remaining}"
                        ));
                    }
                    last_skip_log = now;
                }
                if threshold_held
                    && low_yield_until
                        .map(|until| now >= until)
                        .unwrap_or(true)
                    && last_threshold
                        .map(|last| now.duration_since(last) >= cooldown)
                        .unwrap_or(true)
                {
                    log_event(&format!(
                        "threshold fired percent={percent:.1} threshold={} trigger_seconds={} avail_mb={:.0}",
                        cfg.threshold_percent,
                        cfg.threshold_trigger_seconds,
                        (avail as f64) / (1024.0 * 1024.0)
                    ));
                    let outcome = clean_now("threshold");
                    let cleaned_at = Instant::now();
                    last_threshold = Some(cleaned_at);
                    last_interval = cleaned_at;
                    if outcome.global_freed < LOW_YIELD_FREED_BYTES {
                        let extra = cooldown
                            .checked_mul(LOW_YIELD_COOLDOWN_MULTIPLIER as u32)
                            .unwrap_or(cooldown);
                        low_yield_until = Some(cleaned_at + extra);
                        log_event(&format!(
                            "threshold low_yield freed_mb={:.0} pause_seconds={}",
                            (outcome.global_freed as f64) / (1024.0 * 1024.0),
                            extra.as_secs()
                        ));
                    } else {
                        low_yield_until = None;
                    }
                }
            }
        } else {
            threshold_high_since = None;
            low_yield_until = None;
        }
        if cfg.interval_enabled {
            let interval = Duration::from_secs(cfg.interval_minutes.max(1) * 60);
            if now.duration_since(last_interval) >= interval {
                log_event(&format!("interval fired minutes={}", cfg.interval_minutes.max(1)));
                let _ = clean_now("interval");
                last_interval = Instant::now();
            }
        }
        thread::sleep(Duration::from_secs(5));
    }
}

fn exe_in_same_dir(name: &str) -> Option<PathBuf> {
    let current = env::current_exe().ok()?;
    Some(current.parent()?.join(name))
}

fn format_bytes(bytes: u64) -> String {
    let units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let mut value = bytes as f64;
    let mut unit = 0usize;
    while value >= 1024.0 && unit < units.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{} {}", bytes, units[unit])
    } else {
        format!("{value:.1} {}", units[unit])
    }
}

fn format_usage_tooltip(used: u64, total: u64) -> String {
    if total == 0 {
        return "MemCleaner".to_string();
    }
    let percent = (used.saturating_mul(100) / total).min(100);
    format!(
        "MemCleaner | {}% | {} / {}",
        percent,
        format_bytes(used),
        format_bytes(total),
    )
}

fn current_usage_tooltip() -> String {
    current_memory_usage()
        .map(|(used, total)| format_usage_tooltip(used, total))
        .unwrap_or_else(|| "MemCleaner".to_string())
}

fn tray_labels(cfg: &Config) -> (&'static str, &'static str, &'static str) {
    if cfg.language.eq_ignore_ascii_case("en") {
        ("Open Window", "Clean Now", "Quit")
    } else {
        ("显示主窗口", "立即清理", "退出")
    }
}

fn fill_tray_tip(nid: &mut NOTIFYICONDATAW, text: &str) {
    nid.szTip.fill(0);
    let tip = wide(text);
    let copy_len = tip.len().saturating_sub(1).min(nid.szTip.len().saturating_sub(1));
    nid.szTip[..copy_len].copy_from_slice(&tip[..copy_len]);
}

fn tray_icon_path() -> Option<PathBuf> {
    if let Some(path) = env::var_os("MEMCLEANER_ICON_ICO")
        .map(PathBuf::from)
        .filter(|p| p.exists())
    {
        return Some(path);
    }

    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(current) = env::current_exe().ok() {
        if let Some(parent) = current.parent() {
            candidates.push(parent.join("memcleaner.ico"));
            candidates.push(parent.join("assets").join("memcleaner.ico"));
        }
        for ancestor in current.ancestors() {
            candidates.push(ancestor.join("assets").join("memcleaner.ico"));
            if ancestor.join("Cargo.toml").exists() || ancestor.join("run_memcleaner.py").exists() {
                break;
            }
        }
    }

    candidates.into_iter().find(|p| p.exists())
}

fn launch_gui() {
    if let Some(exe) = env::var_os("MEMCLEANER_GUI_EXE")
        .map(PathBuf::from)
        .filter(|p| p.exists())
    {
        shell_execute(&exe, "");
        return;
    }
    if let Some(exe) = {
        let cfg = load_config();
        (!cfg.gui_exe_path.trim().is_empty())
            .then(|| PathBuf::from(cfg.gui_exe_path.trim()))
            .filter(|p| p.exists())
    } {
        shell_execute(&exe, "");
        return;
    }
    if let Some(exe) = exe_in_same_dir("MemCleaner.exe").filter(|p| p.exists()) {
        shell_execute(&exe, "");
        return;
    }
    if let Some(script) = project_root().map(|p| p.join("run_memcleaner.py")).filter(|p| p.exists()) {
        let python = env::var_os("PYTHON").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("python"));
        let params = format!("\"{}\"", script.display());
        shell_execute(&python, &params);
        return;
    }
    shell_execute(&PathBuf::from("python"), "-m memcleaner");
}

fn project_root() -> Option<PathBuf> {
    let exe = env::current_exe().ok()?;
    for ancestor in exe.ancestors() {
        if ancestor.join("run_memcleaner.py").exists() {
            return Some(ancestor.to_path_buf());
        }
    }
    None
}

fn shell_execute(path: &Path, params: &str) {
    let file = wide(&path.display().to_string());
    let params = wide(params);
    unsafe {
        let _ = ShellExecuteW(
            HWND(null_mut()),
            w!("open"),
            PCWSTR(file.as_ptr()),
            PCWSTR(params.as_ptr()),
            PCWSTR(null()),
            SW_SHOWNORMAL,
        );
    }
}

fn is_admin_process() -> bool {
    unsafe { IsUserAnAdmin().as_bool() }
}

fn quote_arg(arg: &str) -> String {
    if arg.is_empty() || arg.chars().any(|c| c.is_whitespace() || c == '"') {
        format!("\"{}\"", arg.replace('"', "\\\""))
    } else {
        arg.to_string()
    }
}

fn request_admin_restart() -> bool {
    let Ok(exe) = env::current_exe() else {
        return false;
    };
    let file = wide(&exe.display().to_string());
    let args = env::args()
        .skip(1)
        .map(|arg| quote_arg(&arg))
        .collect::<Vec<_>>()
        .join(" ");
    let params = wide(&args);
    unsafe {
        let result = ShellExecuteW(
            HWND(null_mut()),
            w!("runas"),
            PCWSTR(file.as_ptr()),
            PCWSTR(params.as_ptr()),
            PCWSTR(null()),
            SW_SHOWNORMAL,
        );
        result.0 as isize > 32
    }
}

fn add_tray_icon(hwnd: HWND) -> bool {
    let mut nid = NOTIFYICONDATAW {
        cbSize: size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: TRAY_UID,
        uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP,
        uCallbackMessage: WM_TRAY,
        ..Default::default()
    };
    nid.hIcon = load_tray_icon();
    fill_tray_tip(&mut nid, &current_usage_tooltip());
    unsafe { Shell_NotifyIconW(NIM_ADD, &mut nid).as_bool() }
}

fn update_tray_tooltip(hwnd: HWND) {
    let mut nid = NOTIFYICONDATAW {
        cbSize: size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: TRAY_UID,
        uFlags: NIF_TIP,
        ..Default::default()
    };
    fill_tray_tip(&mut nid, &current_usage_tooltip());
    unsafe {
        let _ = Shell_NotifyIconW(NIM_MODIFY, &mut nid);
    }
}

fn load_tray_icon() -> HICON {
    if let Some(path) = tray_icon_path() {
        let icon_path = wide(&path.display().to_string());
        unsafe {
            if let Ok(handle) = LoadImageW(
                None,
                PCWSTR(icon_path.as_ptr()),
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            ) {
                if !handle.is_invalid() {
                    return HICON(handle.0);
                }
            }
        }
    }
    unsafe { LoadIconW(None, IDI_APPLICATION).unwrap_or_default() }
}

fn delete_tray_icon(hwnd: HWND) {
    let mut nid = NOTIFYICONDATAW {
        cbSize: size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: TRAY_UID,
        ..Default::default()
    };
    unsafe {
        let _ = Shell_NotifyIconW(NIM_DELETE, &mut nid);
    }
}

unsafe extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_TRAY => {
            let event = lparam.0 as u32;
            if event == WM_MOUSEMOVE {
                update_tray_tooltip(hwnd);
            } else if event == WM_LBUTTONDBLCLK {
                launch_gui();
            } else if event == WM_RBUTTONUP {
                show_menu(hwnd);
            }
            LRESULT(0)
        }
        WM_COMMAND => {
            match wparam.0 & 0xffff {
                ID_SHOW => launch_gui(),
                ID_CLEAN => {
                    let _ = clean_now("manual");
                }
                ID_QUIT => {
                    RUNNING.store(false, Ordering::Relaxed);
                    delete_tray_icon(hwnd);
                    PostQuitMessage(0);
                }
                _ => {}
            }
            LRESULT(0)
        }
        WM_DESTROY => {
            RUNNING.store(false, Ordering::Relaxed);
            delete_tray_icon(hwnd);
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

#[cfg(test)]
mod tests {
    use super::{cleaning::available_floor, format_bytes, format_usage_tooltip, memory_pressure_high};

    #[test]
    fn format_bytes_uses_binary_units() {
        assert_eq!(format_bytes(512), "512 B");
        assert_eq!(format_bytes(1536), "1.5 KB");
    }

    #[test]
    fn usage_tooltip_includes_percent_and_sizes() {
        let used = 8_u64 * 1024 * 1024 * 1024;
        let total = 16_u64 * 1024 * 1024 * 1024;
        assert_eq!(
            format_usage_tooltip(used, total),
            "MemCleaner | 50% | 8.0 GB / 16.0 GB",
        );
    }

    #[test]
    fn threshold_fires_at_configured_percent() {
        let total = 16_u64 * 1024 * 1024 * 1024;
        let healthy_avail = available_floor(total) * 2;
        assert!(memory_pressure_high(80.0, healthy_avail, total, 80));
        assert!(!memory_pressure_high(79.9, healthy_avail, total, 80));
    }

    #[test]
    fn low_available_memory_fires_even_below_percent() {
        let total = 16_u64 * 1024 * 1024 * 1024;
        let low_avail = available_floor(total).saturating_sub(1);
        assert!(memory_pressure_high(65.0, low_avail, total, 80));
    }
}

unsafe fn show_menu(hwnd: HWND) {
    let menu = CreatePopupMenu().unwrap_or_default();
    let cfg = load_config();
    let (show_label, clean_label, quit_label) = tray_labels(&cfg);
    let show = wide(show_label);
    let clean = wide(clean_label);
    let quit = wide(quit_label);
    let _ = AppendMenuW(menu, MF_STRING, ID_SHOW, PCWSTR(show.as_ptr()));
    let _ = AppendMenuW(menu, MF_STRING, ID_CLEAN, PCWSTR(clean.as_ptr()));
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR(null()));
    let _ = AppendMenuW(menu, MF_STRING, ID_QUIT, PCWSTR(quit.as_ptr()));
    let mut pt = POINT::default();
    let _ = GetCursorPos(&mut pt);
    let _ = SetForegroundWindow(hwnd);
    let _ = TrackPopupMenu(menu, TPM_LEFTALIGN | TPM_BOTTOMALIGN, pt.x, pt.y, 0, hwnd, None);
    let _ = DestroyMenu(menu);
}

fn main() {
    let mut args = env::args_os().skip(1).peekable();
    while let Some(arg) = args.next() {
        if arg == "--gui-exe" {
            if let Some(path) = args.next() {
                env::set_var("MEMCLEANER_GUI_EXE", path);
            }
        }
    }

    if env::args().any(|arg| arg == "--quit") {
        unsafe {
            let Ok(hwnd) = FindWindowW(w!("MemCleanerRustDaemonWindow"), PCWSTR(null())) else {
                return;
            };
            if hwnd.0.is_null() {
                return;
            }
            if PostMessageW(hwnd, WM_COMMAND, WPARAM(ID_QUIT), LPARAM(0)).is_err() {
                log_event("quit failed post_message=false");
                std::process::exit(1);
            }
        }
        return;
    }

    let mutex_name = wide("Local\\MemCleanerRustDaemon");
    unsafe {
        let mutex = CreateMutexW(None, false, PCWSTR(mutex_name.as_ptr())).unwrap_or_default();
        let mutex_error = GetLastError().0;
        if mutex_error == windows::Win32::Foundation::ERROR_ALREADY_EXISTS.0
            || mutex_error == windows::Win32::Foundation::ERROR_ACCESS_DENIED.0
        {
            log_event("ensure ignored existing_daemon=true");
            if !env::args().any(|arg| arg == "--ensure") {
                launch_gui();
            }
            return;
        }
        if mutex.0.is_null() {
            log_event("start failed mutex=false");
            return;
        }

        let startup_cfg = load_config();
        if startup_cfg.auto_elevate && !is_admin_process() {
            let _ = CloseHandle(mutex);
            if request_admin_restart() {
                log_event("elevate requested result=accepted");
                return;
            }
            log_event("elevate requested result=failed_or_cancelled");
        } else {
            let _ = CloseHandle(mutex);
        }

        let mutex = CreateMutexW(None, false, PCWSTR(mutex_name.as_ptr())).unwrap_or_default();
        let mutex_error = GetLastError().0;
        if mutex_error == windows::Win32::Foundation::ERROR_ALREADY_EXISTS.0
            || mutex_error == windows::Win32::Foundation::ERROR_ACCESS_DENIED.0
        {
            log_event("ensure ignored existing_daemon=true");
            if !env::args().any(|arg| arg == "--ensure") {
                launch_gui();
            }
            return;
        }
        if mutex.0.is_null() {
            log_event("start failed mutex=false");
            return;
        }
        let _mutex = OwnedHandle(mutex);

        let hinstance = GetModuleHandleW(None).unwrap_or_default();
        let class_name = w!("MemCleanerRustDaemonWindow");
        let wc = WNDCLASSW {
            style: CS_DBLCLKS,
            lpfnWndProc: Some(wnd_proc),
            hInstance: hinstance.into(),
            lpszClassName: class_name,
            ..Default::default()
        };
        let _ = RegisterClassW(&wc);
        let Ok(hwnd) = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            class_name,
            w!("MemCleaner"),
            WINDOW_STYLE(0),
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        ) else {
            return;
        };
        if hwnd.0.is_null() {
            std::process::exit(1);
        }
        if !add_tray_icon(hwnd) {
            log_event("start failed tray_icon=false");
            std::process::exit(1);
        }
        let cfg = load_config();
        log_event(&format!(
            "start threshold_enabled={} threshold_percent={} threshold_trigger_seconds={} interval_enabled={} interval_minutes={} mode={} auto_elevate={} config={}",
            cfg.threshold_enabled,
            cfg.threshold_percent,
            cfg.threshold_trigger_seconds,
            cfg.interval_enabled,
            cfg.interval_minutes,
            cfg.cleaning_mode,
            cfg.auto_elevate,
            config_path().display()
        ));
        thread::spawn(scheduler_loop);

        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}
