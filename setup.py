#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import print_function

import os.path

from setuptools import find_packages, setup


def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(name='git-issues',
      version='0.0.0',
      author="John Wiegley",
      author_email="johnw@newartisans.com",
      # contributors="Shawn Bohrer, Giulio Eulisse",
      description='A distributed issue tracking system for Git repositories',
      # long_description=read('README.md'),
      url='https://github.com/duplys/git-issues',
      test_suite="tests",
      packages=find_packages(),
      entry_points={
          'console_scripts': [
              'git-issues = git_issues:main'
          ]
      },
      install_requires=['gitshelve'],
      license="BSD",
      classifiers=[
          'Development Status :: 3 - Alpha',
          'Programming Language :: Python :: 2',
          'Programming Language :: Python :: 3',
          'Intended Audience :: Developers',
          'Topic :: Software Development :: Libraries',
          'Operating System :: OS Independent',
          'License :: OSI Approved :: BSD License'
      ]
      )
