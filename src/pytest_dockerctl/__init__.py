import time
import contextlib
import docker
import logging
import pytest
import requests

from typing import Tuple, Callable

from docker.models.containers import Container as DockerContainer


log = logging.getLogger('pytest-dockerctl')


class DockerClientError(Exception):
    """Generic docker API error.
    """


class Container:
    '''
    Wrapper around a ``docker.models.containers.Container`` to include
    log capture and relay through our native logging system and helper
    method(s) for cancellation/teardown.

    '''
    def __init__(
        self,
        cntr: DockerContainer,
    ) -> None:

        self.cntr = cntr
        # log msg de-duplication
        self.seen_so_far = set()

    async def process_logs_until(
        self,
        log_msg_key: str,

        # this is a predicate func for matching log msgs emitted by the
        # underlying containerized app
        patt_matcher: Callable[[str], bool],

        # XXX WARNING XXX: do not touch this sleep value unless
        # you know what you are doing! the value is critical to
        # making sure the caller code inside the startup context
        # does not timeout BEFORE we receive a match on the
        # ``patt_matcher()`` predicate above.
        checkpoint_period: float = 0.001,

    ) -> bool:
        '''
        Attempt to capture container log messages and relay through our
        native logging system.

        '''
        seen_so_far = self.seen_so_far

        while True:
            logs = self.cntr.logs()
            try:
                logs = self.cntr.logs()
            except (
                docker.errors.NotFound,
                docker.errors.APIError
            ):
                log.exception('Failed to parse logs?')
                return False

            entries = logs.decode().split('\n')
            for entry in entries:

                # ignore null lines
                if not entry:
                    continue

                entry = entry.strip()
                try:
                    record = json.loads(entry)
                    msg = record[log_msg_key]
                    level = record['level']

                except json.JSONDecodeError:
                    msg = entry
                    level = 'error'

                # TODO: do we need a more general mechanism
                # for these kinda of "log record entries"?
                # if 'Error' in entry:
                #     raise RuntimeError(entry)

                if (
                    msg
                    and entry not in seen_so_far
                ):
                    seen_so_far.add(entry)
                    getattr(
                        log,
                        level.lower(),
                        log.error
                    )(f'{msg}')

                    if level == 'fatal':
                        raise ApplicationLogError(msg)

                if await patt_matcher(msg):
                    return True

                # do a checkpoint so we don't block if cancelled B)
                await trio.sleep(checkpoint_period)

        return False

    @property
    def cuid(self) -> str:
        fqcn: str = self.cntr.attrs['Config']['Image']
        return f'{fqcn}[{self.cntr.short_id}]'

    def try_signal(
        self,
        signal: str = 'SIGINT',

    ) -> bool:
        try:
            # XXX: market store doesn't seem to shutdown nicely all the
            # time with this (maybe because there are still open grpc
            # connections?) noticably after client connections have been
            # made or are in use/teardown. It works just fine if you
            # just start and stop the container tho?..
            log.cancel(f'SENDING {signal} to {self.cntr.id}')
            self.cntr.kill(signal)
            return True

        except docker.errors.APIError as err:
            if 'is not running' in err.explanation:
                return False

    def hard_kill(self, start: float) -> None:
        delay = time.time() - start
        # get out the big guns, bc apparently marketstore
        # doesn't actually know how to terminate gracefully
        # :eyeroll:...
        log.error(
            f'SIGKILL-ing: {self.cntr.id} after {delay}s\n'
        )
        self.try_signal('SIGKILL')
        self.cntr.wait(
            timeout=3,
            condition='not-running',
        )

    async def cancel(
        self,
        log_msg_key: str,
        stop_predicate: Callable[[str], bool],

        hard_kill: bool = False,

    ) -> None:
        '''
        Attempt to cancel this container gracefully, fail over to
        a hard kill on timeout.

        '''
        cid = self.cntr.id

        # first try a graceful cancel
        log.cancel(
            f'SIGINT cancelling container: {self.cuid}\n'
            'waiting on stop predicate...'
        )
        self.try_signal('SIGINT')

        start = time.time()
        for _ in range(6):

            with trio.move_on_after(1) as cs:
                log.cancel(
                    'polling for CNTR logs for {stop_predicate}..'
                )

                try:
                    await self.process_logs_until(
                        log_msg_key,
                        stop_predicate,
                    )
                except ApplicationLogError:
                    hard_kill = True
                else:
                    # if we aren't cancelled on above checkpoint then we
                    # assume we read the expected stop msg and
                    # terminated.
                    break

            if cs.cancelled_caught:
                # on timeout just try a hard kill after
                # a quick container sync-wait.
                hard_kill = True

            try:
                log.info(f'Polling for container shutdown:\n{cid}')

                if self.cntr.status not in {'exited', 'not-running'}:
                    self.cntr.wait(
                        timeout=0.1,
                        condition='not-running',
                    )

                # graceful exit if we didn't time out
                break

            except (
                ReadTimeout,
            ):
                log.info(f'Still waiting on container:\n{cid}')
                continue

            except (
                docker.errors.APIError,
                ConnectionError,
                requests.exceptions.ConnectionError,
                trio.Cancelled,
            ):
                log.exception('Docker connection failure')
                self.hard_kill(start)
                raise

            except trio.Cancelled:
                log.exception('trio cancelled...')
                self.hard_kill(start)
        else:
            hard_kill = True

        if hard_kill:
            self.hard_kill(start)
        else:
            log.cancel(f'Container stopped: {cid}')


    def has_attr(
        self,
        attr_path: Tuple[str]
    ) -> bool:
        attrs = self.cntr.attrs
        for key in attr_path:
            if key not in attrs:
                return False

            attrs = attrs[key]

        return True

    def waitfor(
        self,
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
            self.cntr.reload()
            val = get(self.cntr.attrs, attr_path)
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
    def run(self, image, command=None, num=1, auto_remove: bool = True, **kwargs):
        """Launch ``num`` docker containers in the background, pulling the image
        first if necessary. Returns a context manager that stops and removes
        all containers on teardown.
        """
        api = self.client.containers
        containers = []
        for _ in range(num):
            cntr = api.run(image, command=command, detach=True, auto_remove=auto_remove, **kwargs)
            container = Container(cntr=cntr)
            log.info("{}:{} Started container"
                     .format(image, container.cntr.short_id))
            containers.append(container)

        for container in containers:
            log.info("{}:{} Waiting on networking and health check...".format(
                image, container.cntr.short_id))
            if 'network' in kwargs and kwargs['network'] == 'host':
                container.waitfor(('NetworkSettings', 'Networks', 'host'))

            else:
                container.waitfor(('NetworkSettings', 'IPAddress'))

            if container.has_attr(('State', 'Health', 'Status')):
                container.waitfor(('State', 'Health', 'Status'), expect='healthy')
        try:
            if len(containers) > 1:
                yield containers

            else:
                yield containers[0]

        finally:
            for container in containers:
                container.cntr.stop()
                log.info("{}:{} Stopped container"
                         .format(image, container.cntr.short_id))
                # dont manually remove cntr, pass auto_remove=True in kwargs
                # container.cntr.remove()
                # log.info("{}:{} Removed container"
                #          .format(image, container.cntr.short_id))


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
