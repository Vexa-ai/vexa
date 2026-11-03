"""One explicit Compose entrypoint; reject modified inputs before any mutation."""
import argparse
import json
import subprocess
from pathlib import Path
from stack import digest


def compose_command(lock, group, services):
    spec = lock['compose_projects'][group]
    if not services or not set(services) <= set(spec['services']):
        raise ValueError('Name explicit services from the deployment lock')
    for path in spec['inputs']:
        if digest(path) != lock['files'][path]:
            raise ValueError('Compose input differs from deployment lock: ' + path)
    args = ['docker', 'compose', '-p', spec['name']]
    for path in spec['inputs']:
        args += ['-f', path]
    return args + ['up', '-d', '--no-deps', '--pull', 'never', *services]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lock', type=Path)
    parser.add_argument('group')
    parser.add_argument('services', nargs='+')
    parser.add_argument('--apply', action='store_true', help='Apply only the explicitly named services')
    parser.add_argument('--recreate', action='store_true', help='Recreate named services after locked mounted-source changes')
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    command = compose_command(lock, args.group, args.services)
    if args.recreate:
        command.insert(command.index('up') + 1, '--force-recreate')
    if args.apply:
        subprocess.run(command, check=True)
    else:
        print(json.dumps({'command': command, 'applied': False}))


if __name__ == '__main__':
    main()
