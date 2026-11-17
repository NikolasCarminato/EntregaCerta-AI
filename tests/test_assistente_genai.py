"""Respostas simuladas para conferir limites, término e contingência, sem rede."""
import unittest
import json
from unittest.mock import patch

from google.genai import types, errors
import httpx
from src import assistente_genai as assistente


def resposta_sdk(motivo='STOP', texto='Resumo completo de teste para a revisão humana da fila.'):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=motivo,
        content=types.Content(role='model', parts=[types.Part(text=texto)]),
    )])


class AssistenteGenAITest(unittest.TestCase):
    def setUp(self):
        self.ambiente=patch.dict(assistente.os.environ,{
            'GEMINI_API_KEY':'chave-ficticia-de-teste', 'GEMINI_MODEL':'gemini-3.6-flash',
        })
        self.ambiente.start();self.addCleanup(self.ambiente.stop)
        self.patch_cliente=patch.object(assistente.genai,'Client')
        self.cliente=self.patch_cliente.start();self.addCleanup(self.patch_cliente.stop)
        self.gerar=self.cliente.return_value.__enter__.return_value.models.generate_content
        espera=patch.object(assistente,'sleep')
        self.espera=espera.start();self.addCleanup(espera.stop)
        jitter=patch.object(assistente,'uniform',return_value=0)
        jitter.start();self.addCleanup(jitter.stop)

    def test_pdf_tem_orcamento_proprio_e_confirma_termino(self):
        self.gerar.return_value=resposta_sdk()
        resultado=assistente.responder('Resumo técnico',{'pedidos':20},para_relatorio=True)
        config=self.gerar.call_args.kwargs['config']
        self.assertEqual(config.max_output_tokens,4096)
        self.assertEqual(config.thinking_config.thinking_level.value,'MINIMAL')
        http=self.cliente.call_args.kwargs['http_options']
        self.assertEqual(http.timeout,45000)
        self.assertEqual(http.retry_options.attempts,1)
        self.assertTrue(resultado['disponivel'])
        self.assertEqual(resultado['motivo_termino'],'STOP')
        self.gerar.assert_called_once()

    def test_limite_de_tokens_refaz_uma_vez_sem_concatenar_fragmentos(self):
        self.gerar.side_effect=[resposta_sdk('MAX_TOKENS','Fragmento incompleto.'),resposta_sdk()]
        resultado=assistente.responder('Resumo técnico',{},para_relatorio=True)
        self.assertTrue(resultado['disponivel'])
        self.assertNotIn('Fragmento',resultado['resposta'])
        self.assertEqual([c.kwargs['config'].max_output_tokens for c in self.gerar.call_args_list],[4096,8192])

    def test_segundo_limite_interrompe_sem_retornar_texto_parcial(self):
        self.gerar.return_value=resposta_sdk('MAX_TOKENS','Fragmento incompleto.')
        resultado=assistente.responder('Resumo técnico',{},para_relatorio=True)
        self.assertFalse(resultado['disponivel'])
        self.assertEqual(resultado['motivo_termino'],'MAX_TOKENS')
        self.assertNotIn('Fragmento',resultado['resposta'])
        self.assertEqual(self.gerar.call_count,2)

    def test_bloqueios_e_falta_de_candidato_nao_sao_repetidos(self):
        for resposta in [resposta_sdk('SAFETY'),resposta_sdk('RECITATION'),types.GenerateContentResponse(candidates=[])]:
            with self.subTest(resposta=resposta):
                self.gerar.reset_mock();self.gerar.return_value=resposta
                resultado=assistente.responder('Resumo técnico',{},para_relatorio=True)
                self.assertFalse(resultado['disponivel'])
                self.gerar.assert_called_once()

    def test_resposta_vazia_e_erro_externo_mantem_contingencia(self):
        self.gerar.return_value=resposta_sdk(texto='')
        self.assertFalse(assistente.responder('Resumo técnico',{},para_relatorio=True)['disponivel'])
        self.gerar.side_effect=TimeoutError()
        self.assertFalse(assistente.responder('Resumo técnico',{},para_relatorio=True)['disponivel'])

    def test_chat_preserva_tokens_e_timeout_sem_retries_aninhados(self):
        self.gerar.return_value=resposta_sdk()
        self.assertTrue(assistente.responder('Pergunta',{})['disponivel'])
        config=self.gerar.call_args.kwargs['config']
        self.assertEqual(config.max_output_tokens,1024)
        self.assertEqual(config.thinking_config.thinking_level.value,'MINIMAL')
        http=self.cliente.call_args.kwargs['http_options']
        self.assertEqual(http.timeout,30000)
        self.assertEqual(http.retry_options.attempts,1)
        self.assertEqual(config.http_options.retry_options.attempts,1)
        self.gerar.assert_called_once()

    def test_modelo_fora_da_familia_3_nao_recebe_thinking_level(self):
        self.gerar.return_value=resposta_sdk()
        with patch.dict(assistente.os.environ,{'GEMINI_MODEL':'gemini-2.5-flash'}):
            self.assertTrue(assistente.responder('Resumo técnico',{},para_relatorio=True)['disponivel'])
        self.assertIsNone(self.gerar.call_args.kwargs['config'].thinking_config)

    def test_outro_gemini_3_usa_low_em_vez_de_minimal(self):
        self.gerar.return_value=resposta_sdk()
        with patch.dict(assistente.os.environ,{'GEMINI_MODEL':'gemini-3.1-pro-preview'}):
            self.assertTrue(assistente.responder('Resumo técnico',{},para_relatorio=True)['disponivel'])
        self.assertEqual(self.gerar.call_args.kwargs['config'].thinking_config.thinking_level.value,'LOW')

    def test_sem_chave_nao_chama_provedor(self):
        with patch.dict(assistente.os.environ,{'GEMINI_API_KEY':''}):
            self.assertFalse(assistente.responder('Resumo técnico',{},para_relatorio=True)['disponivel'])
        self.cliente.assert_not_called()

    def test_fatos_do_chat_aparecem_uma_vez_junto_da_referencia(self):
        self.gerar.return_value=resposta_sdk()
        contexto={'publico':'analista','fonte_dados':'olist', 'pedido':{'peso_total_g':4800},
                  'evidencias_disponiveis':[{'id':'E20','campo':'pedido.peso_total_g','dado':'Peso total (g)','valor':4800}]}
        assistente.responder('Qual o peso?',contexto)
        mensagem=self.gerar.call_args.kwargs['contents']
        corpo=mensagem.split('CONTEXTO ATUAL (dados):\n',1)[1].split('\n\nPERGUNTA:',1)[0]
        recebido=json.loads(corpo)
        self.assertEqual(mensagem.count('4800'),1)
        self.assertEqual(recebido['evidencias_disponiveis'][0]['id'],'E20')
        self.assertNotIn('pedido',recebido)
        self.assertEqual(contexto['pedido']['peso_total_g'],4800)
        assistente.responder('Resumo',contexto,para_relatorio=True)
        self.assertIn('"pedido"',self.gerar.call_args.kwargs['contents'])

    def test_historico_usa_papeis_reais_e_contexto_atual(self):
        self.gerar.return_value=resposta_sdk()
        assistente.responder('E o prazo?', {'pedido': {'prazo_prometido': '2030-01-20'}}, historico=[
            {'papel': 'user', 'texto': 'Qual pedido?'}, {'papel': 'model', 'texto': 'Pedido selecionado.'},
        ])
        conteudos=self.gerar.call_args.kwargs['contents']
        self.assertEqual([m.role for m in conteudos], ['user','model','user'])
        self.assertEqual(conteudos[0].parts[0].text,'Qual pedido?')
        self.assertIn('2030-01-20',conteudos[-1].parts[0].text)

    def test_citacoes_somente_no_chat_com_evidencias(self):
        self.gerar.return_value=resposta_sdk()
        contexto={'evidencias_disponiveis':[{'id':'E6','dado':'Prazo','valor':None}]}
        assistente.responder('Qual prazo?',contexto)
        self.assertIn('cite logo',self.gerar.call_args.kwargs['config'].system_instruction)
        assistente.responder('Resumo do PDF',contexto,para_relatorio=True)
        self.assertNotIn('cite logo',self.gerar.call_args.kwargs['config'].system_instruction)



    def test_desconexao_recuperada_preserva_pergunta_e_historico(self):
        classes=[httpx.RemoteProtocolError]
        if assistente.httpx2 is not None:
            classes.append(assistente.httpx2.RemoteProtocolError)
        for classe in classes:
            with self.subTest(backend=classe.__module__):
                self.gerar.reset_mock();self.espera.reset_mock()
                self.gerar.side_effect=[classe('falha técnica fictícia'),resposta_sdk()]
                r=assistente.responder('E agora?',{},historico=[{'papel':'user','texto':'Antes'}])
                self.assertTrue(r['disponivel']);self.assertTrue(r['recuperada'])
                self.assertEqual(r['tentativas'],2)
                self.assertEqual(r['falhas_tentativas'][0]['categoria'],'conexao')
                self.assertEqual(self.gerar.call_args_list[0].kwargs['contents'],self.gerar.call_args_list[1].kwargs['contents'])
                self.espera.assert_called_once_with(1)

    def test_http_transitorio_recupera_uma_resposta(self):
        for codigo in [408,429,500,502,503,504]:
            with self.subTest(codigo=codigo):
                self.gerar.reset_mock();self.espera.reset_mock()
                self.gerar.side_effect=[errors.APIError(codigo,{'error':{'message':'teste'}}),resposta_sdk()]
                r=assistente.responder('Pergunta',{})
                self.assertTrue(r['recuperada']);self.assertEqual(r['tentativas'],2)
                self.assertEqual(r['falhas_tentativas'][0]['codigo_http'],codigo)
                self.espera.assert_called_once_with(1)

    def test_timeouts_persistentes_param_em_tres_tentativas(self):
        self.gerar.side_effect=httpx.ReadTimeout('dado privado fictício')
        r=assistente.responder('Pergunta',{})
        self.assertFalse(r['disponivel']);self.assertFalse(r['recuperada'])
        self.assertEqual(r['tentativas'],3);self.assertEqual(r['erro_codigo'],'timeout')
        self.assertEqual([c.args[0] for c in self.espera.call_args_list],[1,2])
        self.assertNotIn('privado',str(r))
        self.assertIn('demorou',r['resposta'])

    def test_configuracao_nao_repete_nem_expoe_corpo_do_erro(self):
        for codigo in [400,401,402,403,404]:
            with self.subTest(codigo=codigo):
                self.gerar.reset_mock();self.espera.reset_mock()
                self.gerar.side_effect=errors.APIError(codigo,{'error':{'message':'SEGREDO_FICTICIO_NAO_EXIBIR'}})
                with self.assertLogs(level='WARNING') as logs:
                    r=assistente.responder('Pergunta',{})
                self.gerar.assert_called_once();self.espera.assert_not_called()
                self.assertEqual(r['erro_codigo'],'configuracao')
                self.assertNotIn('SEGREDO_FICTICIO_NAO_EXIBIR',str(r)+str(logs.output))

    def test_retry_after_curto_respeitado_e_longo_sem_repeticao(self):
        for segundos,esperado in [('4',2),('60',1)]:
            with self.subTest(segundos=segundos):
                self.gerar.reset_mock();self.espera.reset_mock()
                erro=errors.APIError(429,{'error':{'message':'quota'}},httpx.Response(429,headers={'Retry-After':segundos}))
                self.gerar.side_effect=[erro,resposta_sdk()]
                r=assistente.responder('Pergunta',{})
                self.assertEqual(r['tentativas'],esperado)
                if esperado==2:
                    self.assertTrue(r['disponivel']);self.espera.assert_called_once_with(4)
                else:
                    self.assertEqual(r['erro_codigo'],'limite');self.espera.assert_not_called()

    def test_retry_info_do_google_e_data_http(self):
        from datetime import datetime,timezone,timedelta
        from email.utils import format_datetime
        detalhes={'error':{'details':[{'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'35.5s'}]}}
        erro=errors.APIError(429,detalhes)
        self.assertEqual(assistente.espera_recomendada(erro),35.5)
        data=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=60),usegmt=True)
        erro=errors.APIError(503,{},httpx.Response(503,headers={'Retry-After':data}))
        self.assertGreater(assistente.espera_recomendada(erro),58)
        self.assertIsNone(assistente.espera_recomendada(errors.APIError(503,{},httpx.Response(503,headers={'Retry-After':'invalido'}))))

    def test_orcamento_de_tempo_impede_nova_tentativa(self):
        self.gerar.side_effect=TimeoutError()
        with patch.object(assistente,'monotonic',side_effect=[0,0,89.5]):
            r=assistente.responder('Pergunta',{})
        self.assertEqual(r['tentativas'],1);self.espera.assert_not_called()
        self.assertFalse(r['disponivel'])

    def test_timeout_por_chamada_respeita_tempo_restante(self):
        self.gerar.return_value=resposta_sdk()
        with patch.object(assistente,'monotonic',side_effect=[0,80]):
            self.assertTrue(assistente.responder('Pergunta',{})['disponivel'])
        self.assertEqual(self.gerar.call_args.kwargs['config'].http_options.timeout,10000)

    def test_pdf_compartilha_limite_com_recuperacao_de_rede(self):
        self.gerar.side_effect=[resposta_sdk('MAX_TOKENS','Fragmento'),httpx.RemoteProtocolError('interrompido'),resposta_sdk()]
        r=assistente.responder('Resumo',{},para_relatorio=True)
        self.assertTrue(r['disponivel']);self.assertTrue(r['recuperada'])
        self.assertEqual(r['tentativas'],3);self.assertNotIn('Fragmento',r['resposta'])
        self.assertEqual([c.kwargs['config'].max_output_tokens for c in self.gerar.call_args_list],[4096,8192,8192])

    def test_recuperacao_nao_duplica_mensagens_da_conversa(self):
        from uuid import uuid4
        from src import conversas_assistente as memoria
        usuario={'id':uuid4(),'perfil':'cliente','csrf':uuid4().hex}
        conversa=memoria.criar(usuario);requisicao=uuid4()
        self.addCleanup(memoria.apagar,conversa,usuario)
        self.gerar.side_effect=[httpx.RemoteProtocolError('interrompido'),resposta_sdk(texto='Pedido em preparação [E5].')]
        contexto={'pedido':{'status':'em_preparacao'}}
        r=memoria.conversar(conversa,usuario,requisicao,'Qual status?',contexto,assistente.responder)
        igual=memoria.conversar(conversa,usuario,requisicao,'Qual status?',contexto,assistente.responder)
        self.assertTrue(r['disponivel']);self.assertEqual(r,igual)
        self.assertEqual(self.gerar.call_count,2)
        self.assertEqual(len(memoria.obter(conversa,usuario).historico),2)

if __name__=='__main__':unittest.main()
