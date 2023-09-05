#!/usr/bin/python3
from setuptools import setup
from setuptools_rust import RustExtension, Binding

setup(
    rust_extensions=[RustExtension(
        "silver_platter._svp_rs", "crates/svp-py/Cargo.toml",
        binding=Binding.PyO3)],)
