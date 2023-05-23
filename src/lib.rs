use std::path::Path;

pub fn derived_branch_name(script: &str) -> &str {
    let first_word = script.split(' ').next().unwrap_or("");
    let script_name = Path::new(first_word).file_stem().unwrap_or_default();
    script_name.to_str().unwrap_or("")
}
