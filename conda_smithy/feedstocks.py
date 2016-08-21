from __future__ import absolute_import, print_function
import argparse
import glob
import multiprocessing
import os

from git import Repo, GitCommandError
from github import Github

from . import github as smithy_github


def feedstock_repos(gh_organization):
    token = smithy_github.gh_token()
    gh = Github(token)
    org = gh.get_organization(gh_organization)
    repos = []
    for repo in org.get_repos():
        if repo.name.endswith('-feedstock'):
            repo.package_name = repo.name.rsplit('-feedstock', 1)[0]
            repos.append(repo)
    return sorted(repos, key=lambda repo: repo.name.lower())


def cloned_feedstocks(feedstocks_directory):
    """
    Return a generator of all cloned feedstocks.
    The feedstock will be generated as an argparse.Namespace and can be used:

        for feedstock in cloned_feedstocks(path_to_feedstocks_directory):
            print(feedstock.name)  # The name of the feedstock, e.g. conda-smithy-feedstock
            print(feedstock.package)  # The name of the package within the feedstock, e.g. conda-smithy
            print(feedstock.directory)  # The absolute path to the repo

    """
    pattern = os.path.abspath(os.path.join(feedstocks_directory, '*-feedstock'))
    for feedstock_dir in sorted(glob.glob(pattern)):
        feedstock_basename = os.path.basename(feedstock_dir)
        feedstock_package = feedstock_basename.rsplit('-feedstock', 1)[0]
        feedstock = argparse.Namespace(name=feedstock_basename,
                                       package=feedstock_package,
                                       directory=feedstock_dir)
        yield feedstock


def fetch_feedstock(repo_dir):
    """Git fetch --all a single git repository."""
    repo = Repo(repo_dir)
    for remote in repo.remotes:
        try:
            remote.fetch()
        except GitCommandError:
            print("Failed to fetch {} from {}.".format(remote.name, remote.url))


def fetch_feedstocks(feedstock_directory):
    """
    Do a git fetch on all of the cloned feedstocks.

    Note: This function uses multiprocessing to parallelise the fetch process.

    """
    feedstocks = list(cloned_feedstocks(feedstock_directory))
    # We pick the minimum of ncpus x10 and total feedstocks for the pool size.
    n_processes = min([len(feedstocks), multiprocessing.cpu_count() * 10])
    pool = multiprocessing.Pool(n_processes)
    for repo in feedstocks:
        repo_dir = repo.directory
        pool.apply_async(fetch_feedstock, args=(repo_dir, ))
    pool.close()
    pool.join()


def feedstocks_list_handle_args(args):
   for repo in feedstock_repos(gh_organization):
        print(repo.name)


def clone_feedstock(feedstock_gh_repo, feedstocks_dir):
    repo = feedstock_gh_repo

    clone_directory = os.path.join(feedstocks_dir, repo.name)
    if not os.path.exists(clone_directory):
        print('Cloning {}'.format(repo.name))
        new_repo = Repo.clone_from(repo.ssh_url, clone_directory)
    clone = Repo(clone_directory)
    if 'upstream' in [remote.name for remote in clone.remotes]:
        clone.delete_remote('upstream')
    clone.create_remote('upstream', url=repo.ssh_url)


def clone_all(gh_org, feedstocks_dir):
    feedstocks = feedstock_repos(gh_org)

    # We pick the minimum of ncpus x10 and total feedstocks for the pool size.
    n_processes = min([len(feedstocks), multiprocessing.cpu_count() * 10])
    pool = multiprocessing.Pool(n_processes)
    for repo in feedstocks:
        pool.apply_async(clone_feedstock, args=(repo, feedstocks_dir))
    pool.close()
    pool.join()


def feedstocks_clone_all_handle_args(args):
    return clone_all(args.organization, args.feedstocks_directory)


def feedstocks_list_cloned_handle_args(args):
    for feedstock in cloned_feedstocks(args.feedstocks_directory):
        print(os.path.basename(feedstock.directory))


def feedstocks_apply_cloned_handle_args(args):
    import subprocess
    for feedstock in cloned_feedstocks(args.feedstocks_directory):
        env = os.environ.copy()
        context = {'FEEDSTOCK_DIRECTORY': feedstock.directory,
                   'FEEDSTOCK_BASENAME': feedstock.name,
                   'FEEDSTOCK_NAME': feedstock.package}
        env.update(context)
        cmd = [item.format(feedstock.directory, feedstock=feedstock, **context) for item in args.cmd]
        print('\nRunning "{}" for {}:'.format(' '.join(cmd), feedstock.package))
        subprocess.check_call(cmd, env=env, cwd=feedstock.directory)


def feedstocks_fetch_handle_args(args):
    return fetch_feedstocks(args.feedstocks_directory)


import jinja2
import six
class NullUndefined(jinja2.Undefined):
    def __unicode__(self):
        return six.text_type(self._undefined_name)

    def __getattr__(self, name):
        return six.text_type('{}.{}'.format(self, name))

    def __getitem__(self, name):
        return '{}["{}"]'.format(self, name)


env = jinja2.Environment(undefined=NullUndefined)
import git

def feedstocks_repos(organization, feedstocks_directory, pull_up_to_date=False, randomise=False, regexp=None):
    """
    Generator of (feedstock, yaml) for each feedstock in the given
    feedstock_directory.

    Parameters
    ----------
    pull_up_to_date : bool (default: True)
        If True, clone all (missing) feedstocks before operation, and fetch
        all feedstocks as each one is being yielded. 
    randomise: bool (default: False)
        If True, randomise the order of the generated feedstocks. This is
        especially useful if no particular priority should be given to
        the "aardvark" feedstock over the "zebra" feedstock ;) .
    regepx: string (default: None)
        If not None, a regular expression which can be used to filter the
        feedstock based on its name. For example '^python' would yield
        only feedstocks starting with the word "python".

    """
    # We can't do anything without having all of the feestocks cloned
    # (the existing clones don't need to be fetched though).
    if pull_up_to_date:
        print('Cloning')
        #clone_all(organization, feedstocks_directory)
        print('Done cloning')

    feedstocks = cloned_feedstocks(feedstocks_directory)

    if regexp:
        import re
        regexp = re.compile(regexp)
        feedstocks = [feedstock for feedstock in feedstocks
                      if regexp.match(feedstock.package)]

    if randomise:
        import random
        feedstocks = list(feedstocks)
        random.shuffle(feedstocks)

    for feedstock in feedstocks:
        repo = git.Repo(feedstock.directory)
        upstream = repo.remotes.upstream

        if pull_up_to_date:
            print('fetching ', feedstock)
            upstream.fetch()

        yield repo, feedstock


def feedstocks_yaml(organization, feedstocks_directory, **feedstocks_repo_kwargs):
    for repo, feedstock in feedstocks_repos(organization, feedstocks_directory, **feedstocks_repo_kwargs):
        upstream = repo.remotes.upstream
        try:
            refs = upstream.refs
        except AssertionError:
            # In early versions of gitpython and empty list of refs resulted in an
            # assertion error (https://github.com/gitpython-developers/GitPython/pull/499).
            refs = []

        if not refs:
            upstream.fetch()
            refs = upstream.refs

        import ruamel.yaml
        for ref in refs:
            remote_branch = ref.remote_head #.replace('{}/'.format(gh_me.login), '')

            content = ref.commit.tree['recipe']['meta.yaml'].data_stream.read()
            parsable_content = env.from_string(content).render(os=os)
            yaml = ruamel.yaml.load(parsable_content, ruamel.yaml.RoundTripLoader)

            yield (feedstock, ref, content, yaml)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    list_feedstocks = subparsers.add_parser('list', help='List all of the feedstocks available on the GitHub organization.')
    list_feedstocks.set_defaults(func=feedstocks_list_handle_args)
    list_feedstocks.add_argument("--organization", default="conda-forge")

    clone_feedstocks = subparsers.add_parser('clone', help='Clone all of the feedstocks available on the GitHub organization.')
    clone_feedstocks.set_defaults(func=feedstocks_clone_all_handle_args)
    clone_feedstocks.add_argument("--organization", default="conda-forge")
    clone_feedstocks.add_argument("--feedstocks-directory", default="./")

    list_cloned_feedstocks = subparsers.add_parser('list-cloned', help='List all of the feedstocks which have been cloned.')
    list_cloned_feedstocks.set_defaults(func=feedstocks_list_cloned_handle_args)
    list_cloned_feedstocks.add_argument("--feedstocks-directory", default="./")

    apply_cloned_feedstocks = subparsers.add_parser('apply-cloned', help='Apply a subprocess to all of the all feedstocks which have been cloned.')
    apply_cloned_feedstocks.set_defaults(func=feedstocks_apply_cloned_handle_args)
    apply_cloned_feedstocks.add_argument("--feedstocks-directory", default="./")
    apply_cloned_feedstocks.add_argument('cmd', nargs='+',
                                         help=('Command arguments, expanded by FEEDSTOCK_NAME, FEEDSTOCK_DIRECTORY, FEEDSTOCK_BASENAME '
                                               'env variables, run with the cwd set to the feedstock.'))

    fetch_feedstocks = subparsers.add_parser('fetch', help='Run git fetch on all of the cloned feedstocks.')
    fetch_feedstocks.set_defaults(func=feedstocks_fetch_handle_args)
    fetch_feedstocks.add_argument("--feedstocks-directory", default="./")

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    main()
