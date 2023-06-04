use pyo3::prelude::*;

#[pyfunction]
fn derived_branch_name(url: &str) -> PyResult<&str> {
    let branch_name = silver_platter::derived_branch_name(url);
    Ok(branch_name)
}

#[pymodule]
fn _svp_rs(py: Python, m: &PyModule) -> PyResult<()> {
    pyo3_log::init();
    m.add_function(wrap_pyfunction!(derived_branch_name, m)?)?;
    Ok(())
}
