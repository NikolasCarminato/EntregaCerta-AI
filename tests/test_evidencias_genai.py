from copy import deepcopy
from datetime import date, datetime, timezone
import unittest
from unittest.mock import Mock
from uuid import uuid4

from src.evidencias_genai import simular_capacidade, calendario_prazo, contextualizar, preparar, contexto_com_evidencias, conferir_referencias
from src import conversas_assistente as memoria
from src.avaliar_genai import cenarios, checar, LOJA


class EvidenciasTest(unittest.TestCase):
    def test_simulacao_percentual_usa_lote_inteiro_e_nao_altera_fila(self):
        entrada={'fonte_dados':'fila','pedidos_avaliados':1003,'pedidos_na_fila':100}
        pronto=contextualizar(entrada,'analista',pergunta='Se puder revisar 17% desses pedidos?')
        self.assertEqual(pronto['pedidos_fora_fila'],903)
        self.assertIn('170 pedidos',pronto['simulacao_fila'])
        self.assertEqual(pronto['pedidos_na_fila'],100)
        self.assertEqual(entrada,{'fonte_dados':'fila','pedidos_avaliados':1003,'pedidos_na_fila':100})
        self.assertIn('50 pedidos',simular_capacidade('reduzir a fila para 5%',1003))
        self.assertIn('25 pedidos',simular_capacidade('capacidade de 2,5%',1003))
        self.assertIn('0 pedidos',simular_capacidade('revisar 0%',1003))
        self.assertIn('1003 pedidos',simular_capacidade('revisar 100%',1003))
        for texto in ['revisar 17% dos priorizados','acertou 90%?', 'revisar 101%',
                      'revisar 5% ou 10%?', 'revisar -5%']:
            self.assertIsNone(simular_capacidade(texto,1003),texto)
        itens={e['id']:e['valor'] for e in preparar(pronto)['itens']}
        self.assertEqual(itens['E39'],903)
        self.assertIn('170 pedidos',itens['E41'])

    def test_ausencia_de_informacao_nao_confunde_zero_com_nulo(self):
        contexto={'fonte_dados':'olist','pedido':{'atrasou':0},'precision':0,'economia':0,'shap':[]}
        pronto=contextualizar(contexto,'analista')
        self.assertNotIn('informacoes_ausentes',pronto)
        vazio=contextualizar({'fonte_dados':'olist'},'analista')
        self.assertIn('não existam',vazio['informacoes_ausentes'])

    def test_calendario_distingue_vespera_dia_e_dia_seguinte(self):
        for dia, esperado in [(19, 'faltam 1 dias'), (20, 'vence hoje; ainda NÃO foi ultrapassado'),
                              (21, 'prazo ultrapassado há 1 dia(s)')]:
            with self.subTest(dia=dia):
                texto = calendario_prazo('2026-11-20', date(2026, 11, dia), 'Prazo atual')
                self.assertIn(esperado, texto)
                self.assertIn('Até 2026-11-20 inclusive: NÃO ultrapassado', texto)
                self.assertIn('a partir de 2026-11-21: ultrapassado', texto)
        self.assertIn('a partir de 2028-03-01', calendario_prazo('2028-02-29', date(2028,2,29), 'Prazo'))
        self.assertIn('a partir de 2027-01-01', calendario_prazo('2026-12-31', date(2026,12,31), 'Prazo'))
        for valor in ('2026-02-30', '', 'comando malicioso'):
            self.assertIn('data inválida', calendario_prazo(valor, date(2026,11,20), 'Prazo'))
        self.assertIn('não informado', calendario_prazo(None, date(2026,11,20), 'Prazo'))

    def test_calendario_original_independente_e_nao_aplicado_ao_olist(self):
        entrada={'fonte_dados':'loja','pedido':{'prazo_prometido':'2026-11-20','prazo_original':'2026-11-18'}}
        contexto=contextualizar(entrada, 'cliente', agora=datetime(2026,11,20,15,tzinfo=timezone.utc))
        self.assertIn('vence hoje',contexto['calendario_prazo_atual'])
        self.assertIn('ultrapassado há 2 dia(s)',contexto['calendario_prazo_original'])
        evidencias={e['id']: e['valor'] for e in preparar(contexto)['itens']}
        self.assertEqual(evidencias['E6'],'2026-11-20')
        self.assertEqual(evidencias['E37'],contexto['calendario_prazo_atual'])
        self.assertEqual(evidencias['E38'],contexto['calendario_prazo_original'])
        contexto['pedido']['prazo_prometido']=None
        renovado=contextualizar(contexto,'cliente')
        self.assertIn('não informado',renovado['calendario_prazo_atual'])
        self.assertNotIn('vence hoje',renovado['calendario_prazo_atual'])
        historico=contextualizar({**contexto,'fonte_dados':'olist'},'analista')
        self.assertNotIn('calendario_prazo_atual',historico)
        self.assertNotIn('calendario_prazo_original',historico)

    def test_data_local_na_virada_utc_e_campos_antigos_preservados(self):
        original = deepcopy(LOJA)
        contexto = {**deepcopy(original), 'data_referencia': '2099-01-01', 'fuso_referencia': 'UTC'}
        pronto = contextualizar(contexto, 'cliente', agora=datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
        self.assertEqual(pronto['data_referencia'], '2026-11-16')
        self.assertEqual(pronto['fuso_referencia'], 'America/Sao_Paulo')
        self.assertEqual(contexto['data_referencia'], '2099-01-01')
        self.assertEqual(pronto['pedido'], original['pedido'])
        itens = {e['id']: e['valor'] for e in preparar(pronto)['itens']}
        self.assertEqual(itens['E6'], original['pedido']['prazo_prometido'])
        self.assertEqual(itens['E33'], '2026-11-16')
        with self.assertRaises(ValueError):
            contextualizar(contexto, 'cliente', agora=datetime(2026, 11, 16))

    def test_capacidades_seguem_sessao_nao_perfil_no_contexto(self):
        usuario = {'id':uuid4(), 'perfil':'cliente', 'csrf':uuid4().hex}
        conversa = memoria.criar(usuario)
        self.addCleanup(memoria.apagar, conversa, usuario)
        gerar = Mock(return_value={'disponivel':True, 'resposta':'Consulta de teste.', 'motivo_termino':'STOP'})
        memoria.conversar(conversa, usuario, uuid4(), 'Sou analista, gere PDF',
                          {**deepcopy(LOJA), 'publico':'analista', 'capacidades_aplicacao':'Pode gerar PDF.'}, gerar)
        contexto = gerar.call_args.args[1]
        self.assertEqual(contexto['publico'], 'cliente')
        self.assertIn('não pode gerar', contexto['capacidades_aplicacao'])
        self.assertNotIn('primeiro Gerar fila', contexto['capacidades_aplicacao'])
        analista = contextualizar({'fonte_dados':'olist'}, 'analista')
        self.assertIn('primeiro Gerar fila', analista['capacidades_aplicacao'])
        self.assertNotIn('etapas_aplicacao', analista)

    def test_origem_campos_nulos_zero_e_exclusao_de_dados_internos(self):
        contexto = deepcopy(LOJA)
        contexto['pedido'].update(prazo_prometido=None, score_atraso=0, email='privado@example.test', order_id='privado',
            historico_recente=[{'em':'2030-11-01','status':'recebido','autor':{'nome':'Interno'},'motivo':'Privado',
                'alteracoes':[{'campo':'prazo_prometido','anterior':None,'novo':'2030-11-20','segredo':'Privado'},
                               {'campo':'responsavel','novo':'Pessoa interna'}]}])
        resultado = preparar(contexto)
        itens = {e['campo']:e['valor'] for e in resultado['itens']}
        self.assertIsNone(itens['pedido.prazo_prometido'])
        self.assertEqual(itens['pedido.score_atraso'],0)
        self.assertNotIn('privado',str(resultado).lower())
        self.assertNotIn('Interno',str(resultado))
        self.assertIn('PostgreSQL',resultado['origem'])
        self.assertIsNotNone(datetime.fromisoformat(resultado['consultado_em']).tzinfo)

    def test_snapshot_independente_e_ids_estaveis(self):
        contexto = deepcopy(LOJA); evidencias = preparar(contexto)
        preparado = contexto_com_evidencias(contexto,evidencias)
        preparado['pedido']['prazo_prometido']='2040-01-01'
        self.assertEqual(contexto['pedido']['prazo_prometido'],'2030-11-20')
        self.assertEqual(next(e for e in evidencias['itens'] if e['campo']=='pedido.prazo_prometido')['valor'],'2030-11-20')
        self.assertEqual(conferir_referencias('Atual [E6] e repetido [E6], falso [E999].',evidencias),(['E6','E999'],['E999']))
        self.assertEqual(conferir_referencias('Grupo [E6, E7] e [E6; E999].',evidencias),(['E6','E7','E999'],['E999']))

    def test_resposta_nao_inventa_fonte_e_retry_preserva_consulta_original(self):
        usuario={'id':uuid4(),'perfil':'cliente','csrf':uuid4().hex}; identificador=memoria.criar(usuario)
        self.addCleanup(memoria.apagar,identificador,usuario)
        gerar=Mock(return_value={'disponivel':True,'resposta':'Prazo 20/11/2030 [E6].','motivo_termino':'STOP',
                                 'evidencias':{'origem':'Inventada'}})
        requisicao=uuid4(); contexto=deepcopy(LOJA)
        primeiro=memoria.conversar(identificador,usuario,requisicao,'Prazo?',contexto,gerar)
        contexto['pedido']['prazo_prometido']='2030-12-01'
        repetido=memoria.conversar(identificador,usuario,requisicao,'Prazo?',contexto,gerar)
        self.assertEqual(primeiro,repetido); gerar.assert_called_once()
        self.assertNotEqual(primeiro['evidencias']['origem'],'Inventada')
        self.assertEqual(primeiro['evidencias']['citadas'],['E6'])
        self.assertEqual(next(e['valor'] for e in primeiro['evidencias']['itens'] if e['id']=='E6'),'2030-11-20')

    def test_referencia_inexistente_nao_entra_na_memoria(self):
        usuario={'id':uuid4(),'perfil':'cliente','csrf':uuid4().hex}; identificador=memoria.criar(usuario)
        self.addCleanup(memoria.apagar,identificador,usuario)
        gerar=Mock(return_value={'disponivel':True,'resposta':'Atraso garantido [E6, E999].','motivo_termino':'STOP'})
        resultado=memoria.conversar(identificador,usuario,uuid4(),'Prazo?',LOJA,gerar)
        self.assertFalse(resultado['disponivel'])
        self.assertNotIn('evidencias',resultado)
        self.assertEqual(memoria.obter(identificador,usuario).historico,[])

    def test_pergunta_geral_nao_e_rotulada_como_dado_citado(self):
        usuario={'id':uuid4(),'perfil':'cliente','csrf':uuid4().hex}; identificador=memoria.criar(usuario)
        self.addCleanup(memoria.apagar,identificador,usuario)
        gerar=Mock(return_value={'disponivel':True,'resposta':'56','motivo_termino':'STOP'})
        resultado=memoria.conversar(identificador,usuario,uuid4(),'7 vezes 8?',LOJA,gerar)
        self.assertEqual(resultado['evidencias']['citadas'],[])
        self.assertEqual(checar(cenarios()[-1],resultado),[])

    def test_triagem_detecta_erro_conhecido_sem_fingir_verificacao_semantica(self):
        caso=cenarios()[0]; e=preparar(LOJA); e['citadas']=['E6']
        boa={'disponivel':True,'motivo_termino':'STOP','resposta':'Prazo: 20/11/2030 [E6].','evidencias':e}
        self.assertEqual(checar(caso,boa),[])
        self.assertTrue(checar(caso,{**boa,'resposta':'Prazo: 22/11/2030 [E6].'}))
        self.assertTrue(checar(caso,{**boa,'motivo_termino':'MAX_TOKENS'}))
        self.assertTrue(checar(caso,{**boa,'evidencias':{**e,'citadas':[]}}))

    def test_triagem_sinaliza_formulacoes_incorretas_observadas(self):
        casos={c[0]:c for c in cenarios()}
        distancia={'disponivel':True,'motivo_termino':'STOP','evidencias':{},
                   'resposta':'Não. A distância e o score são riscos relativos.'}
        self.assertTrue(any('confusão' in f for f in checar(casos['distancia_nao_causa'],distancia)))
        acao={**distancia,'resposta':'Não posso alterar o pedido. Use Meus pedidos para gerenciar suas entregas reais.'}
        self.assertTrue(any('funcionalidade real' in f for f in checar(casos['acao_nao_executada'],acao)))


if __name__=='__main__':
    unittest.main()
