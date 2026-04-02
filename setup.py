from setuptools import setup, find_packages

# Metadata for the Patient Patch app.  This file enables installation
# of the app via ``bench get-app`` by providing the package name and
# module discovery.  Without this, older versions of bench may refuse
# to install the repository because they expect a setup.py file.

setup(
    name="patient_patch",
    version="1.0.0",
    description="Patient Patch",
    author="Dagaar",
    author_email="info.dagaar@gmail.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=[],
)