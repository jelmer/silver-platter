use breezyshim::Prober;
use pyo3::prelude::*;

pub fn select_probers(vcs_type: &str) -> Vec<Prober> {
    pyo3::Python::with_gil(|py| {
        let probersm = py.import("silver_platter.probers").unwrap();
        let select_probers = probersm.getattr("select_probers").unwrap();
        select_probers
            .call1((vcs_type,))
            .unwrap()
            .extract::<Vec<PyObject>>()
            .map(|probers| probers.into_iter().map(Prober::new).collect::<Vec<_>>())
            .unwrap()
    })
}

pub fn select_preferred_probers(vcs_type: &str) -> Vec<Prober> {
    pyo3::Python::with_gil(|py| {
        let probersm = py.import("silver_platter.probers").unwrap();
        let select_preferred_probers = probersm.getattr("select_preferred_probers").unwrap();
        select_preferred_probers
            .call1((vcs_type,))
            .unwrap()
            .extract::<Vec<PyObject>>()
            .map(|probers| probers.into_iter().map(Prober::new).collect::<Vec<_>>())
            .unwrap()
    })
}
