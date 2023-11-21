from setuptools import find_packages, setup

setup(
    name='terraform-github-actions',
    version='1.36.0',
    packages=find_packages('src'),
    package_dir={'': 'src'},
    package_data={'terraform_version': ['backend_constraints.json']},
    entry_points={
        'console_scripts': [
            'github_pr_comment=github_pr_comment.__main__:main',
            'lock-info=lock_info.__main__:main'
        ]
    },
    install_requires=[
        'requests',
        'python-hcl2',
        'canonicaljson'
    ]
)
