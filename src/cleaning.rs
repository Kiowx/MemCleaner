use std::collections::HashSet;

pub fn normalize_process_name(name: &str) -> String {
    name.trim().to_ascii_lowercase()
}

pub fn parse_excluded_names(raw: &str) -> HashSet<String> {
    let mut excluded = HashSet::new();
    for part in raw.split(',') {
        let name = normalize_process_name(part);
        if name.is_empty() {
            continue;
        }
        excluded.insert(name.clone());
        if !name.contains('.') {
            excluded.insert(format!("{name}.exe"));
        }
    }
    excluded
}

pub fn cleaning_mode_min_ws(mode: &str) -> u64 {
    let mib = 1024_u64 * 1024;
    match mode {
        "conservative" => 512 * mib,
        "aggressive" => 64 * mib,
        _ => 192 * mib,
    }
}

pub fn critical_protected_names() -> HashSet<&'static str> {
    [
        "system",
        "registry",
        "memory compression",
        "idle",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "dwm.exe",
        "fontdrvhost.exe",
        "securityhealthservice.exe",
        "securityhealthsystray.exe",
    ]
    .into_iter()
    .collect()
}

pub fn comfort_protected_names() -> HashSet<&'static str> {
    [
        "explorer.exe",
        "sihost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "textinputhost.exe",
        "searchhost.exe",
        "taskmgr.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "windowsterminal.exe",
        "conhost.exe",
        "code.exe",
        "devenv.exe",
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "steam.exe",
        "steamwebhelper.exe",
    ]
    .into_iter()
    .collect()
}

pub fn protected_names_for_mode(mode: &str) -> HashSet<&'static str> {
    let mut names = critical_protected_names();
    if mode != "aggressive" {
        names.extend(comfort_protected_names());
    }
    names
}

pub fn available_floor(total: u64) -> u64 {
    let gib = 1024_u64 * 1024 * 1024;
    (total.saturating_mul(8) / 100).clamp(512 * 1024 * 1024, 2 * gib)
}

pub fn memory_pressure_high(percent: f64, avail: u64, total: u64, threshold: u64) -> bool {
    if total == 0 {
        return percent >= threshold as f64;
    }
    let floor = available_floor(total);
    percent >= threshold as f64 || avail <= floor
}
