from setuptools import setup, find_packages

from codecs import open
from os import path


#data_dir = path.abspath(path.dirname('__here__'))


setup(name = 'multiplierz',
      version = '2.0',
      description = 'The multiplierz proteomics package',
      author = 'William Max Alexander (et al.)',
      author_email = 'williamM_alexander@dfci.harvard.edu',
      classifiers = ['Development Status :: 4 - Beta',
                     'Intended Audience :: Science/Research',
                     'Topic :: Scientific/Engineering :: Bio-Informatics',
                     'License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)',
                     'Programming Language :: Python :: 2.7',
                     ],
      keywords = 'biology bioinformatics proteomics spectrometry',
      packages = find_packages(),
      include_package_data=True,
      install_requires = ['numpy', 'comtypes', 'matplotlib', 'pypiwin32',
                          'openpyxl', 'xlrd', 'xlwt', 'requests'] # Removed 'lxml'.
      )