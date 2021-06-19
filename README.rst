pytest-dockerctl
================
`Docker`_ control integrated with ``pytest`` for `system testing`_.

``pytest-dockerctl`` is a `pytest`_ plugin for managing all things
docker using the `docker-py`_  API.

Install
*******
For now install from this repo:
::
    pip install git+git://github.com/tgoodlet/pytest-dockerctl.git

Usage
*****
Provides a ``dockerctl`` fixture for spinning up and tearing down containers:

.. code-block:: python

    import pytest
    import subprocess

    @pytest.fixture
    def server_addrs(dockerctl):
        addrs = []

        # spin up 4 of the same container
        with dockerctl.run(
            'docker_repo/my_image'
            volumes={'/local/config/path': {'bind': '/etc/server/config'}},
            num=4,
        ) as containers:
            for container in containers:
                addrs.append(container.attrs['NetworkSettings']['IPAddress'])

            yield addrs

        # all containers are stopped and removed when the context manager exits

    def test_startup_ping(server_addrs):
        for addr in server_addrs:
            subprocess.check_call("ping -c 1 {}".format(addr))


The ``dockerctl`` object is a ``DockerCtl`` instance which (for now) is
just a simple wrapper around a docker-py `DockerClient`_. You can access
the underlying client via ``dockerctl.client``.

.. links
.. _Docker: https://docs.docker.com/
.. _system testing: https://en.wikipedia.org/wiki/System_testing
.. _pytest: https://docs.pytest.org
.. _docker-py: https://github.com/docker/docker-py
.. _DockerClient: https://docker-py.readthedocs.io/en/stable/client.html#client-reference
.. _HEALTHCHECK: https://docs.docker.com/engine/reference/builder/#healthcheck
