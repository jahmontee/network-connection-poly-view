"""One explicit GitHub request per socket activation; no timers or saved state."""
import json
import socket
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from core import Problem
from module_manager import canonical
from repository_manager import GitHubClient,repository_url,asset_url,creator_url


def fetch(message,client=None):
    if not isinstance(message,dict) or message.keys()-{'action','slug','url','owner'}:raise Problem('Unsupported repository request.')
    if message.get('action')=='creator-repositories' and set(message)=={'action','owner'}:
        owner=message['owner']
        if not isinstance(owner,str):raise Problem('Choose a GitHub creator.')
        normalized,_=creator_url('https://github.com/'+owner)
        if normalized!=owner:raise Problem('Invalid creator identity.')
        return canonical((client or GitHubClient()).creator_repositories(owner))
    slug=message.get('slug')
    if not isinstance(slug,str):raise Problem('Choose a saved repository.')
    normalized,_=repository_url('https://github.com/'+slug)
    if normalized!=slug:raise Problem('Invalid repository identity.')
    client=client or GitHubClient()
    if message.get('action')=='releases' and set(message)=={'action','slug'}:return canonical(client.releases(slug))
    if message.get('action')=='download' and set(message)=={'action','slug','url'}:
        return client.download(asset_url(message['url'],slug),slug)
    raise Problem('Choose Check for updates or a release download.')


def serve(connection):
    connection.settimeout(35)
    with connection.makefile('rb') as request:
        line=request.readline(2049)
        try:
            if len(line)>2048 or not line.endswith(b'\n'):raise Problem('Invalid repository request.')
            raw=fetch(json.loads(line))
            connection.sendall(canonical(dict(length=len(raw)))+b'\n'+raw)
        except (Problem,ValueError,TypeError) as error:
            connection.sendall(canonical(dict(error=str(error)[:400],status=getattr(error,'status',400)))+b'\n')


if __name__=='__main__':
    with socket.fromfd(0,socket.AF_UNIX,socket.SOCK_STREAM) as connection:serve(connection)
