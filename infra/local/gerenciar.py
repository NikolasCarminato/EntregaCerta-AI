"""Docker + Terraform local. Execute com a Python da .venv, a partir de qualquer pasta."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from dotenv import dotenv_values

RAIZ = Path(__file__).resolve().parents[2]
PASTA = Path(__file__).resolve().parent
BANCO = 'entregacerta_ai_postgres_db'
APP = 'entregacerta_ai_app'
PROTEGIDOS = {'docker_container.postgres', 'docker_volume.dados', 'docker_network.rede'}


def executar(args, *, ambiente=None, mostrar=False, permitir_falha=False):
    """Argumentos estruturados, sem shell e sem imprimir variáveis secretas."""
    resultado = subprocess.run(args, cwd=RAIZ, env=ambiente, text=True,
                               encoding='utf-8', errors='replace', capture_output=not mostrar)
    if resultado.returncode and not permitir_falha:
        # Logs de Terraform/Docker podem conter o ambiente do banco: não os ecoar.
        acao = args[2] if args[1].startswith('-chdir=') else args[1]
        raise RuntimeError(f'{Path(args[0]).name} {acao} falhou (código {resultado.returncode}).')
    return resultado


def inspecionar(tipo, nome):
    r = executar(['docker', tipo, 'inspect', nome], permitir_falha=True)
    return json.loads(r.stdout)[0] if r.returncode == 0 else None


def validar_plano(plano, banco_existente):
    """A adoção não pode alterar nem substituir recursos persistentes existentes."""
    alteracoes = []
    for recurso in plano.get('resource_changes', []):
        endereco = recurso['address']
        acoes = recurso['change']['actions']
        if endereco in PROTEGIDOS:
            if 'delete' in acoes or (banco_existente and acoes != ['no-op']):
                raise RuntimeError(f'Plano recusado: {endereco} seria alterado. Banco preservado.')
        if acoes != ['no-op']:
            alteracoes.append((endereco, acoes))
    return alteracoes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('acao', choices=['planejar', 'iniciar', 'parar'])
    p.add_argument('--porta', type=int, default=8001)
    p.add_argument('--sem-build', action='store_true', help='Reutilizar imagem local já construída.')
    args = p.parse_args()
    if not 1024 <= args.porta <= 65535:
        raise ValueError('Use uma porta entre 1024 e 65535.')
    executar(['docker', 'info'], permitir_falha=False)
    if args.acao == 'parar':
        executar(['docker', 'stop', APP])
        print('Aplicação parada. PostgreSQL e volume preservados.')
        return
    valores = dotenv_values(RAIZ / '.env')
    for chave in ['POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD']:
        if not valores.get(chave):
            raise ValueError(f'Preencha {chave} no .env; não cole a senha no terminal.')
    tf = shutil.which('terraform')
    if not tf:
        portatil = RAIZ / '.terraform-local' / ('terraform.exe' if os.name == 'nt' else 'terraform')
        tf = str(portatil) if portatil.is_file() else None
    if not tf:
        raise ValueError('Instale Terraform >= 1.14 ou coloque o executável em .terraform-local/. Veja o guia.')

    # Descobrir volume/rede do banco existente ANTES de qualquer criação.
    banco = inspecionar('container', BANCO)
    rede, volume = 'entregacertaai_default', 'entregacertaai_postgres_data'
    if banco:
        mounts = [m for m in banco['Mounts'] if m['Destination'] == '/var/lib/postgresql/data']
        if len(mounts) != 1 or mounts[0]['Type'] != 'volume':
            raise ValueError('Montagem do banco diferente da esperada; nenhum recurso foi alterado.')
        volume = mounts[0]['Name']
        redes = banco['NetworkSettings']['Networks']
        if len(redes) != 1:
            raise ValueError('Banco com múltiplas redes: revise a configuração antes de adotar.')
        rede = next(iter(redes))
        existentes = dict(item.split('=', 1) for item in banco['Config']['Env'] if '=' in item)
        if any(existentes.get(k) != valores[k] for k in ['POSTGRES_USER','POSTGRES_DB','POSTGRES_PASSWORD']):
            raise ValueError('Credenciais do .env diferem da configuração original do contêiner; revise sem recriar o banco.')
        if not banco['State']['Running']:
            executar(['docker', 'start', BANCO])
        imagem_postgres = banco['Image']
        print('Banco existente identificado; contêiner, rede e volume serão preservados.', flush=True)
    else:
        if inspecionar('volume', volume):
            raise ValueError('Volume existente sem o contêiner esperado: restauração deve ser revisada antes de prosseguir.')
        executar(['docker', 'pull', 'postgres:17'], mostrar=True)
        imagem_postgres = inspecionar('image', 'postgres:17')['Id']

    if not args.sem_build:
        executar(['docker', 'build', '--tag', 'entregacerta-ai:local', '.'], mostrar=True)
    imagem = inspecionar('image', 'entregacerta-ai:local')
    if not imagem:
        raise ValueError('Imagem ausente: execute novamente sem --sem-build.')
    contexto = executar(['docker', 'context', 'show']).stdout.strip()
    config = dict(raiz=RAIZ.as_posix(), contexto_docker=contexto,
                  imagem_app=imagem['Id'], imagem_postgres=imagem_postgres,
                  rede=rede, volume=volume, usuario=valores['POSTGRES_USER'],
                  banco=valores['POSTGRES_DB'], porta_app=args.porta)
    ambiente = {**os.environ, 'TF_IN_AUTOMATION':'1', 'TF_VAR_config':json.dumps(config),
                'TF_VAR_senha_postgres':valores['POSTGRES_PASSWORD']}
    comando = [tf, f'-chdir={PASTA}']
    def terraform(*a): return executar(comando + list(a), ambiente=ambiente)
    terraform('init', '-input=false', '-no-color')
    terraform('validate', '-no-color')
    print('Terraform inicializado e configuração validada.', flush=True)
    # Na primeira execução ainda não existe um estado local para consultar.
    estado = set(terraform('state', 'list').stdout.splitlines()) if (PASTA / 'terraform.tfstate').is_file() else set()
    recursos = [('docker_network.rede', 'network', rede),
                ('docker_volume.dados', 'volume', volume),
                ('docker_container.postgres', 'container', BANCO)]
    for endereco, tipo, nome in recursos:
        objeto = inspecionar(tipo, nome)
        if endereco not in estado and objeto:
            identificador = objeto.get('Id') or objeto.get('ID') or objeto['Name']
            terraform('import', '-input=false', '-no-color', endereco, identificador)
            print('Importado sem recriar:', endereco, flush=True)
    # Um contêiner de aplicação homônimo fora deste estado não será substituído.
    aplicacao = inspecionar('container', APP)
    if 'docker_container.aplicacao' not in estado and aplicacao:
        raise ValueError('Aplicação já existe fora deste estado Terraform. Revise antes de adotar.')
    if args.acao == 'iniciar' and aplicacao and not aplicacao['State']['Running']:
        executar(['docker', 'start', APP])
    terraform('plan', '-input=false', '-no-color', '-out=local.tfplan')
    plano = json.loads(terraform('show', '-json', 'local.tfplan').stdout)
    alteracoes = validar_plano(plano, bool(banco))
    print('Plano:', alteracoes or 'nenhuma alteração', flush=True)
    if args.acao == 'planejar':
        print('Plano conferido. Use iniciar para aplicar. Estado/plano são locais e não devem ser publicados.')
        return
    terraform('apply', '-input=false', '-no-color', 'local.tfplan')
    # Um stop manual não exige recriar o contêiner para voltar a funcionar.
    aplicacao = inspecionar('container', APP)
    if aplicacao and not aplicacao['State']['Running']:
        executar(['docker', 'start', APP])
    print(f'Aplicação: http://localhost:{args.porta}', flush=True)
    if not banco:
        print('Banco novo e vazio. Carregue-o conforme o guia antes de usar os fluxos autenticados.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as erro:
        print(str(erro), file=sys.stderr)
        sys.exit(1)
