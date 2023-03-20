import time
import contextlib
import docker
import logging
import pytest
import requests

from typing import Tuple

from docker.models.containers import Container


log = logging.getLogger('pytest-dockerctl')


class DockerClientError(Exception):
    """Generic docker API error.
    """

def has_attr(
    cntr: Container,
    attr_path: Tuple[str]
) -> bool:
    attrs = cntr.attrs
    for key in attr_path:
        if key not in attrs:
            return False

        attrs = attrs[key]

    return True


def waitfor(
    cntr: Container,
    attr_path: Tuple[str],
    expect=None,
    timeout=20
):
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
        raise TimeoutError("{} failed to be {}, value: \"{}\"".format(
            attr_path, expect if expect else 'not None', val))


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
            log.info("{}:{} Started container"
                     .format(image, container.short_id))
            containers.append(container)

        for container in containers:
            log.info("{}:{} Waiting on networking and health check...".format(
                image, container.short_id))
            if 'network' in kwargs and kwargs['network'] == 'host':
                waitfor(container, ('NetworkSettings', 'Networks', 'host'))

            else:
                waitfor(container, ('NetworkSettings', 'IPAddress'))

            if has_attr(container, ('State', 'Health', 'Status')):
                waitfor(container, ('State', 'Health', 'Status'), expect='healthy')
        try:
            yield containers
        finally:
            for container in containers:
                container.stop()
                log.info("{}:{} Stopped container"
                         .format(image, container.short_id))
                container.remove()
                log.info("{}:{} Removed container"
                         .format(image, container.short_id))


@pytest.hookimpl
def pytest_addoption(parser):
    '''Parse user specified Docker url.
    '''
    parser.addoption(
        "--docker-url", action="store", dest='dockerurl',
        default=None,
        help="Base URL for talking to the Docker engine. "
        "Example 'unix:///var/run/docker.sock' or 'tcp://127.0.0.1:1234'."
    )
    parser.addoption(
        "--skip-no-docker", action="store_true", dest='skipnodocker',
        default=False,
        help="Skip any test that relies on the `dockerctl` fixture when set."
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
        reason = ("Could not connect to a Docker daemon? "
                  "Make sure pytest has root permissions.")
        log.error(reason)
        if request.config.option.skipnodocker:
            pytest.skip(reason)
        return None
