"""Regras com fixtures técnicas; reprodução com registros reais, identificada à parte."""
import json
import os
import unittest
from unittest.mock import Mock, patch
from io import BytesIO

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from pypdf import PdfReader

from src import catboost_ver07 as cb, api, acesso_web, relatorio_pdf
from src.inferencia import carregar_modelos, prever_pedidos, priorizar_pedidos
from src.evidencias_genai import contextualizar


class PoliticaCatBoostTest(unittest.TestCase):
    def exemplo(self):
        # Dados artificiais exclusivamente para regras de código, sem métricas ML.
        return pd.DataFrame({'order_id':list('abcdef'),
            'checkpoint_at':pd.to_datetime(['2018-01-01']*3+['2018-01-02']*2+['2018-01-03']),
            'ordem_original':[2,1,3,4,5,6], 'pendente':[True]*5+[False],
            'elegivel':[True]*6, 'score_atraso':[.8,.8,.7,.1,.2,1.]})

    def test_cota_por_dia_e_desempate_independente_da_ordem_recebida(self):
        df = self.exemplo()
        a = priorizar_pedidos(df, .65).set_index('order_id')
        b = priorizar_pedidos(df.sample(frac=1, random_state=7), .65).set_index('order_id')
        pd.testing.assert_series_equal(a.prioridade.sort_index(), b.prioridade.sort_index())
        self.assertEqual(a.loc[a.prioridade.eq(1)].index.tolist(), ['a','b','d','e'])
        self.assertEqual(a.loc['f','prioridade'], 0)
        self.assertEqual(priorizar_pedidos(df, 0).prioridade.sum(), 0)
        self.assertEqual(priorizar_pedidos(df, 1).prioridade.sum(), 5)
        for fracao in [-1, 2, float('nan')]:
            with self.assertRaises(ValueError): priorizar_pedidos(df, fracao)

    def test_previsao_so_pontua_pendentes_sem_alvos(self):
        df = self.exemplo()
        df['valor'] = np.arange(6)
        df['categoria'] = ['x']*6
        pacote = {'tipo':'catboost_ver07','colunas_entrada':['valor','categoria'],'colunas_categoricas':['categoria']}
        modelo = Mock()
        modelo.predict_proba.return_value = np.array([[.3,.7]]*5)
        r = prever_pedidos(df, pacote, modelo)
        self.assertEqual(modelo.predict_proba.call_args.args[0].columns.tolist(), ['valor','categoria'])
        self.assertEqual(len(modelo.predict_proba.call_args.args[0]), 5)
        self.assertEqual(r.iloc[-1].score_atraso, 0)
        self.assertEqual(r.iloc[-1].situacao_modelo, 'entregue_antes_do_checkpoint')
        with self.assertRaises(ValueError): prever_pedidos(df.drop(columns='valor'),pacote,modelo)
        with self.assertRaises(ValueError): prever_pedidos(pd.concat([df,df.iloc[:1]]),pacote,modelo)

    def test_fatos_do_relatorio_e_simulacao_usam_denominador_diario(self):
        r = cb.priorizar(self.exemplo(), .65)
        meta = cb.descrever({'particao_padrao':'teste'},r)
        dados = {**meta,'pedidos_avaliados':6,'pedidos_na_fila':4,'fracao_fila':.65,
                 'fila':r.loc[r.prioridade.eq(1),['order_id','score_atraso','prioridade']].to_dict('records')}
        self.assertTrue(cb.validar_cota(dados))
        fatos = relatorio_pdf.indicadores(dados)
        self.assertIn('pendentes',fatos['base_capacidade'])
        contexto = contextualizar({**dados,'fonte_dados':'fila'},'analista',pergunta='Quero revisar 65%')
        self.assertIn('4 alertas',contexto['simulacao_fila'])
        dados['limites_diarios'][0]['selecionados']=1
        with self.assertRaises(ValueError): relatorio_pdf.indicadores(dados)


@unittest.skipUnless((cb.PASTA/'pedidos_d1.parquet').is_file(), 'Artefatos reais VER0.7 não preparados')
class ReproducaoRealCatBoostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pacote, cls.modelo = carregar_modelos('catboost_ver07')
        cls.entrada = pd.read_parquet(cb.PASTA/'pedidos_d1.parquet')
        cls.ref = pd.read_csv(cb.PASTA/'auditoria_previsoes.csv')

    def test_inferencia_real_val_e_teste_reproduzem_scores_e_alertas(self):
        self.assertFalse({'late','atrasou','order_delivered_customer_date'} & set(self.entrada))
        for parte in ('validacao','teste'):
            df = self.entrada.loc[self.entrada.particao.eq(parte)]
            r = priorizar_pedidos(prever_pedidos(df,self.pacote,self.modelo),.65)
            ref = self.ref.loc[self.ref.particao.eq(parte)]
            self.assertEqual(r.order_id.tolist(),ref.order_id.tolist())
            np.testing.assert_allclose(r.score_atraso,ref.score_atraso,atol=1e-12,rtol=0)
            np.testing.assert_array_equal(r.prioridade,ref.prioridade)
            self.assertEqual(int(r.prioridade.sum()),680 if parte=='validacao' else 990)

    def test_mlp_anterior_continua_carregando(self):
        p,m = carregar_modelos('mlp')
        self.assertIn('preprocessador',p)
        self.assertFalse(m.training)


@unittest.skipUnless(os.getenv('ENTREGACERTA_TEST_POSTGRES')=='1', 'Integração PostgreSQL opcional')
class BackendCatBoostRealTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ENTREGACERTA_MODELO':'catboost_ver07','ENTREGACERTA_PARTICAO':'teste'})
        self.env.start();self.addCleanup(self.env.stop)
        api.obter_modelos.cache_clear();api.obter_lote_pedidos.cache_clear()
        self.addCleanup(api.obter_modelos.cache_clear);self.addCleanup(api.obter_lote_pedidos.cache_clear)

    def test_postgresql_api_pdf_e_genai_sem_expor_alvos(self):
        p,m = carregar_modelos()
        df = cb.consultar(p)
        self.assertFalse({'late','atrasou','order_delivered_customer_date','score_atraso'} & set(df))
        r = priorizar_pedidos(prever_pedidos(df,p,m),p['fracao_fila'])
        ref = pd.read_csv(cb.PASTA/'auditoria_previsoes.csv').query("particao == 'teste'")
        np.testing.assert_allclose(r.score_atraso,ref.score_atraso,rtol=0,atol=1e-12)
        np.testing.assert_array_equal(r.prioridade,ref.prioridade)
        usuario = {'id':'analista-tecnico','perfil':'analista','csrf':'csrf-teste'}
        with TestClient(api.app,headers={'Origin':'http://127.0.0.1:8000','X-CSRF-Token':'csrf-teste'}) as client, patch.object(acesso_web,'obter_sessao',return_value=usuario):
            resumo = client.get('/api/prioridades')
            self.assertEqual(resumo.status_code,200,resumo.text[:300])
            dados = resumo.json()
            self.assertEqual((dados['pedidos_avaliados'],dados['pendentes_avaliados'],dados['pedidos_na_fila']),(18603,1486,990))
            self.assertTrue(cb.validar_cota(dados))
            pagina = client.get('/api/pedidos').json()
            self.assertEqual(pagina['total'],18603)
            self.assertNotIn('atrasou',pagina['pedidos'][0])
            self.assertEqual(pagina['politica_fila'],'cota_diaria_pendentes')
            with patch.object(relatorio_pdf,'responder',return_value={'disponivel':False}):
                pdf = client.post('/api/analista/relatorio-pdf',json={'analise_id':dados['analise_id']})
            self.assertEqual(pdf.status_code,200,pdf.text[:100] if not pdf.content.startswith(b'%PDF') else '')
            texto=' '.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
            self.assertIn('18.603',texto);self.assertIn('pendentes',texto);self.assertIn('65,0%',texto)
            contexto=api.contexto_assistente(usuario)
            self.assertEqual(contexto['pendentes_avaliados'],1486)
            self.assertIn('ceil',contexto['regra'])
