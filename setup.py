from setuptools import setup


with open('README.rst') as f:
    readme = f.read()


setup(
     name="pytest-dockerctl",
     version='0.1.alpha',
     description='A pytest plugin managing containers using the docker-py API',
     long_description=readme,
     license='MIT',
     author='Tyler Goodlet',
     author_email='tgoodlet@gmail.com',
     url='https://github.com/tgoodlet/pytest-dockerctl',
     platforms=['linux'],
     packages=['pytest_dockerctl'],
     entry_points={'pytest11': [
         'dockerctl = pytest_dockerctl'
     ]},
     zip_safe=False,
     install_requires=['docker'],
     classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: POSIX',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: MacOS :: MacOS X',
        'Topic :: Software Development :: Testing',
        'Programming Language :: Python :: 3',
     ],
)
