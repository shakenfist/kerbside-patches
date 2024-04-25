#!/usr/bin/python

import jinja2

JOBS = {
    'functional-tests': [
        {
            'name': 'rocky-9-kolla',
            'baseimage': 'sf://label/ci-images/rocky-9',
            'baseuser': 'cloud-user',
            'targets': [
                'kolla-2023.1', 'kolla-2023.2', 'kolla'
                ]
        },
        {
            'name': 'debian-12-kolla',
            'baseimage': 'sf://label/ci-images/debian-12',
            'baseuser': 'debian',
            'targets': [
                'kolla-2023.1', 'kolla-2023.2', 'kolla'
                ]
        },
        {
            'name': 'rocky-9-kolla-ansible',
            'baseimage': 'sf://label/ci-images/rocky-9',
            'baseuser': 'cloud-user',
            'targets': [
                'kolla-ansible-2023.1', 'kolla-ansible-2023.2', 'kolla-ansible'
                ]
        },
        {
            'name': 'debian-12-kolla-ansible',
            'baseimage': 'sf://label/ci-images/debian-12',
            'baseuser': 'debian',
            'targets': [
                'kolla-ansible-2023.1', 'kolla-ansible-2023.2', 'kolla-ansible'
                ]
        },
        {
            'name': 'rocky-9-nova',
            'baseimage': 'sf://label/ci-images/rocky-9',
            'baseuser': 'cloud-user',
            'targets': ['nova-2023.1', 'nova-2023.2', 'nova-2024.1', 'nova']
        },
        {
            'name': 'debian-12-nova',
            'baseimage': 'sf://label/ci-images/debian-12',
            'baseuser': 'debian',
            'targets': ['nova-2023.1', 'nova-2023.2', 'nova-2024.1', 'nova']
        },
        {
            'name': 'rocky-9-openstacksdk',
            'baseimage': 'sf://label/ci-images/rocky-9',
            'baseuser': 'cloud-user',
            'targets': [
                'openstacksdk-2023.1', 'openstacksdk-2023.2',
                'openstacksdk-2024.1', 'openstacksdk'
                ]
        },
        {
            'name': 'debian-12-openstacksdk',
            'baseimage': 'sf://label/ci-images/debian-12',
            'baseuser': 'debian',
            'targets': [
                'openstacksdk-2023.1', 'openstacksdk-2023.2',
                'openstacksdk-2024.1', 'openstacksdk'
                ]
        },
        {
            'name': 'rocky-9-oslo.config',
            'baseimage': 'sf://label/ci-images/rocky-9',
            'baseuser': 'cloud-user',
            'targets': [
                'oslo.config-2023.1', 'oslo.config-2023.2',
                'oslo.config-2024.1', 'oslo.config'
                ]
        },
        {
            'name': 'debian-12-oslo.config',
            'baseimage': 'sf://label/ci-images/debian-12',
            'baseuser': 'debian',
            'targets': [
                'oslo.config-2023.1', 'oslo.config-2023.2',
                'oslo.config-2024.1', 'oslo.config'
                ]
        },
        {
            'name': 'rocky-9-python-novaclient',
            'baseimage': 'sf://label/ci-images/rocky-9',
            'baseuser': 'cloud-user',
            'targets': [
                'python-novaclient-2023.1', 'python-novaclient-2023.2',
                'python-novaclient-2024.1', 'python-novaclient'
                ]
        },
        {
            'name': 'debian-12-python-novaclient',
            'baseimage': 'sf://label/ci-images/debian-12',
            'baseuser': 'debian',
            'targets': [
                'python-novaclient-2023.1', 'python-novaclient-2023.2',
                'python-novaclient-2024.1', 'python-novaclient'
                ]
        },
        {
            'name': 'rocky-9-python-openstackclient',
            'baseimage': 'sf://label/ci-images/rocky-9',
            'baseuser': 'cloud-user',
            'targets': [
                'python-openstackclient-2023.1', 'python-openstackclient-2023.2',
                'python-openstackclient-2024.1', 'python-openstackclient'
                ]
        },
        {
            'name': 'debian-12-python-openstackclient',
            'baseimage': 'sf://label/ci-images/debian-12',
            'baseuser': 'debian',
            'targets': [
                'python-openstackclient-2023.1', 'python-openstackclient-2023.2',
                'python-openstackclient-2024.1', 'python-openstackclient'
                ]
        },
    ],
}


if __name__ == '__main__':
    for style in JOBS.keys():
        with open('%s.tmpl' % style) as f:
            t = jinja2.Template(f.read())

        for job in JOBS[style]:
            with open('%s-%s.yml' % (style, job['name']), 'w') as f:
                f.write(t.render(job))
