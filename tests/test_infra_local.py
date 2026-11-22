"""Proteção do banco durante a adoção de recursos pelo Terraform local."""
import unittest

from infra.local.gerenciar import PROTEGIDOS, validar_plano


def plano(endereco, acoes):
    return {'resource_changes': [{'address': endereco, 'change': {'actions': acoes}}]}


class ProtecaoInfraTest(unittest.TestCase):
    def test_adocao_sem_alteracoes(self):
        for recurso in PROTEGIDOS:
            self.assertEqual(validar_plano(plano(recurso, ['no-op']), True), [])

    def test_banco_existente_nao_pode_ser_criado_atualizado_ou_substituido(self):
        for recurso in PROTEGIDOS:
            for acoes in [['create'], ['update'], ['delete'], ['delete', 'create'], ['create', 'delete']]:
                with self.subTest(recurso=recurso, acoes=acoes), self.assertRaises(RuntimeError):
                    validar_plano(plano(recurso, acoes), True)

    def test_instalacao_nova_pode_criar_recursos(self):
        for recurso in PROTEGIDOS:
            self.assertEqual(validar_plano(plano(recurso, ['create']), False), [(recurso, ['create'])])

    def test_remocao_do_banco_bloqueada_tambem_em_instalacao_nova(self):
        for recurso in PROTEGIDOS:
            with self.assertRaises(RuntimeError):
                validar_plano(plano(recurso, ['delete']), False)

    def test_atualizar_aplicacao_nao_exige_recriar_banco(self):
        dados = {'resource_changes': [
            {'address': 'docker_container.postgres', 'change': {'actions': ['no-op']}},
            {'address': 'docker_container.aplicacao', 'change': {'actions': ['delete', 'create']}},
        ]}
        self.assertEqual(validar_plano(dados, True), [('docker_container.aplicacao', ['delete', 'create'])])
