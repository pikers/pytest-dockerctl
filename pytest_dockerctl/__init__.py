import time
import contextlib
import docker
import logging
import pytest
import requests


log = logging.getLogger('pytest-dockerctl')


class DockerClientError(Exception):
    """Generic docker API error.
    """


def waitfor(cntr, attr_path, expect=None, timeout=20):
    """Wait for a container's attr value to be set.
    If ``expect`` is provided wait for the value to be set to that value.
    """
    def get(val, path):
        for key in path:
            val = val[key]
        return val

    start = time.time()
    while time.time() - start < timeout:
        cntr.reload()
        val = get(cntr.attrs, attr_path)
        if expect is None and val:
            return val
        elif val == expect:
            return val
    else:
        raise TimeoutError("{} failed to be {}".format(
            attr_path, expect if expect else 'not None'))


class DockerCtl(object):
    """Control for the docker-py ``DockerClient` for the purposes of
    system testing dockerized software with pytest.
    """
    def __init__(self, url=None, **kwargs):
        self.client = docker.DockerClient(
            base_url=url, **kwargs) if url else docker.from_env(**kwargs)

    @contextlib.contextmanager
    def run(self, image, command=None, num=1, **kwargs):
        """Launch ``num`` docker containers in the background, pulling the image
        first if necessary. Returns a context manager that stops and removes
        all containers on teardown.
        """
        api = self.client.containers
        containers = []
        for _ in range(num):
            container = api.run(image, command=command, detach=True, **kwargs)
            log.info("Started {} {}...".format(image, container.short_id))
            containers.append(container)

        for container in containers:
            waitfor(container, ('NetworkSettings', 'IPAddress'))
            waitfor(container, ('State', 'Health', 'Status'), expect='healthy')
        try:
            yield containers
        finally:
            for container in containers:
                container.stop()
                container.remove()
                log.info("Stopped {} {}...".format(image, container.short_id))


@pytest.hookimpl
def pytest_addoption(parser):
    '''Parse user specified Docker url.
    '''
    parser.addoption(
        "--docker_url", action="store", dest='dockerurl',
        default=None,
        help="Base URL for talking to the Docker engine. "
        "Example 'unix:///var/run/docker.sock' or 'tcp://127.0.0.1:1234'."
    )


@pytest.fixture(scope='session')
def dockerctl(request):
    """An instance of a docker-py ``DockerClient`` wrapper.
    """
    try:
        dockerctl = DockerCtl(request.config.option.dockerurl)
        dockerctl.client.ping()
        return dockerctl
    except requests.ConnectionError:
        pytest.skip("Could not connect to a Docker daemon?")
