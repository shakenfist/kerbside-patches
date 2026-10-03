import unittest

from kolla_ansible_launcher import args


class ScanTestCase(unittest.TestCase):
    def test_repeated_inventory_in_every_form(self):
        argv = ['deploy', '-i', 'one', '--inventory', 'two', '--inventory=three', '-i=four', '-ifive']
        self.assertEqual(
            [('-i', 'one'), ('--inventory', 'two'), ('--inventory', 'three'), ('-i', 'four'), ('-i', 'five')],
            args.scan(args.KOLLA_ANSIBLE, argv))

    def test_extra_vars_files(self):
        argv = ['deploy', '-e', '@vars.yml', '-e@more.yml', '--extra-vars=@third.yml', '-e', 'key=value',
                '-ekey2=value2', '-e', '{"a": "@b"}']
        self.assertEqual(
            [('-e', 'vars.yml'), ('-e', 'more.yml'), ('--extra-vars', 'third.yml')],
            args.scan(args.KOLLA_ANSIBLE, argv))

    def test_vault_id(self):
        argv = ['deploy', '--vault-id', 'prod@/srv/vault/prod', '--vault-id=dev@prompt', '--vault-id', 'plain',
                '--vault-id', 'prompt']
        self.assertEqual(
            [('--vault-id', '/srv/vault/prod'), ('--vault-id', 'plain')],
            args.scan(args.KOLLA_ANSIBLE, argv))

    def test_other_path_options(self):
        argv = ['deploy', '-p', 'play.yml', '--playbook', 'other.yml', '--vault-password-file', 'v1',
                '--vault-pass-file=v2', '--passwords', 'pw.yml', '--log-file', 'log', '--configdir', 'cfg']
        self.assertEqual(
            ['play.yml', 'other.yml', 'v1', 'v2', 'pw.yml', 'log', 'cfg'],
            [path for _, path in args.scan(args.KOLLA_ANSIBLE, argv)])

    def test_values_of_other_options_are_skipped(self):
        # The value of --limit is never mistaken for an option.
        argv = ['deploy', '--limit', '-i', '-t', '-p', '-i', 'real']
        self.assertEqual([('-i', 'real')], args.scan(args.KOLLA_ANSIBLE, argv))

    def test_abbreviated_long_options_are_not_recognised(self):
        self.assertEqual([], args.scan(args.KOLLA_ANSIBLE, ['deploy', '--invent', 'inv']))

    def test_double_dash_ends_options(self):
        self.assertEqual([], args.scan(args.KOLLA_ANSIBLE, ['deploy', '--', '-i', 'inv']))

    def test_option_without_value(self):
        self.assertEqual([], args.scan(args.KOLLA_ANSIBLE, ['deploy', '-i']))

    def test_password_tools(self):
        self.assertEqual([('-p', 'pw.yml')], args.scan(args.GENPWD, ['-p', 'pw.yml']))
        self.assertEqual(
            [('--old', 'a'), ('--new', 'b'), ('--final', 'c')],
            args.scan(args.MERGEPWD, ['--old', 'a', '--new=b', '--final', 'c', '--clean']))
        self.assertEqual(
            [('-p', 'pw.yml'), ('-c', 'ca.pem')],
            args.scan(args.READPWD, ['-v', 'https://vault:8200', '-kv', 'kv', '-kvp', 'p', '-ppw.yml', '-cca.pem']))
        self.assertEqual(
            [('--vault-cacert', 'ca.pem')],
            args.scan(args.WRITEPWD, ['--vault-addr', 'x', '--vault-cacert=ca.pem', '-t', 'tok']))


class HostPathsTestCase(unittest.TestCase):
    def test_relative_paths_against_cwd(self):
        argv = ['deploy', '-i', 'inv', '-i', '../other/inv', '-e', '@./vars/x.yml', '-i', '/abs/inv']
        self.assertEqual(
            ['/home/op/work/inv', '/home/op/other/inv', '/home/op/work/vars/x.yml', '/abs/inv'],
            args.host_paths(args.KOLLA_ANSIBLE, argv, '/home/op/work'))

    def test_password_tool_default(self):
        self.assertEqual(['/etc/kolla/passwords.yml'], args.host_paths(args.GENPWD, [], '/tmp'))
        self.assertEqual(['/tmp/pw.yml'], args.host_paths(args.GENPWD, ['-p', 'pw.yml'], '/tmp'))
        self.assertEqual(['/tmp/pw.yml'], args.host_paths(args.READPWD, ['--passwords=pw.yml', '-v', 'x'], '/tmp'))
        self.assertEqual([], args.host_paths(args.MERGEPWD, [], '/tmp'))


class ConfigdirTestCase(unittest.TestCase):
    def test_default(self):
        self.assertEqual('/etc/kolla', args.configdir(args.KOLLA_ANSIBLE, ['deploy'], {}, '/tmp'))

    def test_from_environment(self):
        environ = {'KOLLA_CONFIG_PATH': 'cfg'}
        self.assertEqual('/home/op/cfg', args.configdir(args.KOLLA_ANSIBLE, ['deploy'], environ, '/home/op'))
        self.assertEqual('/home/op/cfg', args.configdir(args.GENPWD, ['-p', 'x'], environ, '/home/op'))

    def test_argument_beats_environment(self):
        environ = {'KOLLA_CONFIG_PATH': '/srv/env'}
        argv = ['deploy', '--configdir', '/srv/first', '--configdir=/srv/last']
        self.assertEqual('/srv/last', args.configdir(args.KOLLA_ANSIBLE, argv, environ, '/'))

    def test_password_tools_ignore_configdir_argument(self):
        self.assertEqual('/etc/kolla', args.configdir(args.GENPWD, ['--configdir', '/srv/x'], {}, '/'))


class SubcommandTestCase(unittest.TestCase):
    def test_subcommand(self):
        self.assertEqual((0, 'deploy'), args.subcommand(['deploy', '-i', 'x']))
        self.assertEqual((3, 'install-deps'), args.subcommand(['-v', '--log-file', 'l', 'install-deps']))
        self.assertEqual((None, None), args.subcommand(['--version']))
        self.assertEqual((None, None), args.subcommand([]))


if __name__ == '__main__':
    unittest.main()
