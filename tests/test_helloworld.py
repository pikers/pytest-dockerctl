import time

async def test_hello(dockerctl):
    echo_str = 'hello pytest_dockerctl!'
    with dockerctl.run('bash', command=f'bash -c \'echo \"{echo_str}\" && sleep 1\'') as container:
        assert echo_str + '\n' == container.cntr.logs().decode('utf-8')
