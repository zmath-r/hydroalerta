import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import sqlite3
import math

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="HydroAlerta Pro (Crowdsourcing)", page_icon="🌊", layout="wide")

st.title("🌊 HydroAlerta Lite + Crowdsourcing Cidadão")
st.write("Análise preditiva multivariável com validação de dados da comunidade em tempo real.")

# --- PARÂMETROS FIXOS ---
CODIGO_ESTACAO_NIVEL = "19500000"
CODIGO_ESTACAO_CHUVA = "08051017"
CIDADE_MONITORADA = "Macapá"
ESTADO_MONITORADO = "AP"

VIAS_CRITICAS = {
    "Entorno do Canal do Jandiá": {"vulnerabilidade": "Muito Alta", "fator_reducao": 0.75},
    "Av. Mendonça Furtado (Proximidades do Canal)": {"vulnerabilidade": "Alta", "fator_reducao": 0.85},
    "Bairro Congós (Zonas Baixas de Ressaca)": {"vulnerabilidade": "Muito Alta", "fator_reducao": 0.75},
    "Bairro Beirol (Áreas de Acúmulo)": {"vulnerabilidade": "Alta", "fator_reducao": 0.85},
    "Bairro Jesus de Nazaré": {"vulnerabilidade": "Média", "fator_reducao": 0.90},
    "Centro (Áreas Impermeabilizadas)": {"vulnerabilidade": "Média", "fator_reducao": 0.95},
    "Outras vias / Áreas Altas": {"vulnerabilidade": "Normal", "fator_reducao": 1.00}
}

# --- 🗄️ CONFIGURAÇÃO DO BANCO DE DADOS (SQLite) ---

def inicializar_banco():
    conn = sqlite3.connect("historico_hydroalerta_v3.db")
    cursor = conn.cursor()
    # Tabela original de simulações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leituras_risco (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT,
            via_monitorada TEXT,
            chuva_mm REAL,
            nivel_rio_m REAL,
            estado_mare TEXT,
            risco_calculado TEXT
        )
    ''')
    # NOVA Tabela de Crowdsourcing
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS relatos_alagamento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local TEXT,
            nivel_alagamento TEXT,
            observacao TEXT,
            data_hora TEXT
        )
    ''')
    conn.commit()
    conn.close()

def salvar_leitura(via, chuva, rio, mare, risco):
    conn = sqlite3.connect("historico_hydroalerta_v3.db")
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO leituras_risco (data_hora, via_monitorada, chuva_mm, nivel_rio_m, estado_mare, risco_calculado)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (data_atual, via, chuva, rio, mare, risco))
    conn.commit()
    conn.close()

def salvar_relato_cidadao(local, nivel, obs):
    conn = sqlite3.connect("historico_hydroalerta_v3.db")
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO relatos_alagamento (local, nivel_alagamento, observacao, data_hora)
        VALUES (?, ?, ?, ?)
    ''', (local, nivel, obs, data_atual))
    conn.commit()
    conn.close()

def verificar_relatos_recentes(via):
    """Verifica se há relatos de alagamento Alto ou Médio na via nas últimas 6 horas."""
    conn = sqlite3.connect("historico_hydroalerta_v3.db")
    limite_tempo = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    query = f"""
        SELECT COUNT(*) FROM relatos_alagamento 
        WHERE local = '{via}' 
        AND data_hora >= '{limite_tempo}'
        AND (nivel_alagamento = 'Alto' OR nivel_alagamento = 'Médio')
    """
    cursor = conn.cursor()
    cursor.execute(query)
    quantidade = cursor.fetchone()[0]
    conn.close()
    return quantidade > 0

inicializar_banco()

# --- FUNÇÕES DE CONEXÃO COM AS APIs ---
# (Mantidas idênticas à versão anterior para simplificar a leitura)
@st.cache_data(ttl=3600) 
def buscar_nivel_rio_ana(cod_estacao):
    hoje = datetime.now()
    url = f"http://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos?codEstacao={cod_estacao}&dataInicio={(hoje - timedelta(days=3)).strftime('%d/%m/%Y')}&dataFim={hoje.strftime('%d/%m/%Y')}"
    try:
        root = ET.fromstring(requests.get(url, timeout=15).content)
        for dado in root.iter():
            if 'Nivel' in dado.tag and dado.text: return float(dado.text) / 100
    except: pass
    return None

@st.cache_data(ttl=3600) 
def buscar_chuva_real_ana(cod_estacao):
    hoje = datetime.now()
    url = f"http://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos?codEstacao={cod_estacao}&dataInicio={(hoje - timedelta(days=3)).strftime('%d/%m/%Y')}&dataFim={hoje.strftime('%d/%m/%Y')}"
    try:
        root = ET.fromstring(requests.get(url, timeout=15).content)
        for dado in root.iter():
            if 'Chuva' in dado.tag and dado.text: return float(dado.text)
    except: pass
    return None

@st.cache_data(ttl=3600)
def buscar_coordenadas(cidade):
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={cidade}&count=1&language=pt&format=json"
    try:
        response = requests.get(url).json()
        if "results" in response: return response["results"][0]["latitude"], response["results"][0]["longitude"]
    except: pass
    return 0.0, 0.0

@st.cache_data(ttl=3600)
def buscar_previsao_chuva(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&timezone=auto"
    try:
        response = requests.get(url).json()
        if "daily" in response: return response["daily"]["precipitation_sum"][0]
    except: pass
    return 0.0

@st.cache_data(ttl=1800)
def buscar_dados_mare(lat, lon, api_key=""):
    hora_atual = datetime.now()
    ciclo = math.sin((hora_atual.hour + hora_atual.minute/60) * (math.pi / 6)) 
    hora_futura = hora_atual + timedelta(hours=1)
    ciclo_futuro = math.sin((hora_futura.hour + hora_futura.minute/60) * (math.pi / 6))
    
    if ciclo > 0.7: estado = "Maré Alta"
    elif ciclo < -0.7: estado = "Maré Baixa"
    elif ciclo_futuro > ciclo: estado = "Maré Subindo"
    else: estado = "Maré Descendo"
    
    return estado, 1.5 + (ciclo * 1.5)


# --- 🧠 LÓGICA DE RISCO COM INTEGRAÇÃO DE CROWDSOURCING ---

def calcular_risco_com_mare_e_relatos(chuva, nivel_rio, estado_mare, lim_c_alto, lim_r_alto, lim_c_med, lim_r_med, tem_relato_recente):
    """Calcula o risco integrando dados ambientais e relatos da comunidade."""
    
    # Se a comunidade reportou alagamento recentemente, o modelo é forçado a elevar o nível de alerta
    # independentemente do que os sensores dizem (calibração de campo).
    
    if tem_relato_recente:
        if chuva >= lim_c_alto or nivel_rio >= lim_r_alto:
            return "MÁXIMO", "🚨 **ALERTA MÁXIMO:** Cidadãos reportaram alagamentos recentes nesta via e os indicadores ambientais continuam críticos. Evite a região!", "error"
        else:
            return "ALTO", "⚠️ **ALTO RISCO (Confirmado pela População):** Embora os sensores não estejam no limite máximo, moradores registraram acúmulo de água nas últimas horas. Atenção redobrada.", "error"

    # Regras padrão (sem relatos recentes)
    if nivel_rio < lim_r_med and chuva < lim_c_med:
        return "BAIXO", "✅ **RISCO BAIXO:** Condições operando em segurança.", "success"
    
    if chuva >= lim_c_alto and nivel_rio >= lim_r_alto and estado_mare == "Maré Alta":
        return "MÁXIMO", "🚨 **ALERTA PREVENTIVO MÁXIMO:** Combinação crítica detectada! (Chuva + Rio Alto + Maré Alta).", "error"

    if nivel_rio >= lim_r_alto and estado_mare in ["Maré Subindo", "Maré Alta"]:
        return "MUITO ALTO", "⚠️ **RISCO MUITO ALTO:** O rio já ultrapassou a cota e a maré está causando represamento.", "error"

    if chuva >= lim_c_alto or nivel_rio >= lim_r_alto:
        return "ALTO", "⚠️ **ALTO RISCO:** As cotas limite da via foram ultrapassadas.", "error"

    if chuva >= lim_c_med or nivel_rio >= lim_r_med:
        if estado_mare in ["Maré Subindo", "Maré Alta"]:
            return "MÉDIO", "🟠 **RISCO MÉDIO-ALTO:** Indicadores medianos, mas maré desfavorável impede o escoamento.", "warning"
        return "MÉDIO", "🟡 **RISCO MÉDIO:** Atenção para acúmulo de águas. Escoamento ocorrendo.", "warning"

    return "BAIXO", "✅ **Condições Normais.**", "success"


# --- PROCESSAMENTO AUTOMÁTICO DE DADOS ---
lat, lon = buscar_coordenadas(CIDADE_MONITORADA)
chuva_prevista_api = buscar_previsao_chuva(lat, lon)
nivel_rio_real = buscar_nivel_rio_ana(CODIGO_ESTACAO_NIVEL)
chuva_real_ana = buscar_chuva_real_ana(CODIGO_ESTACAO_CHUVA)
estado_mare, altura_mare = buscar_dados_mare(lat, lon)

st.sidebar.markdown("---")
st.sidebar.write(f"📍 **Município:** {CIDADE_MONITORADA} - {ESTADO_MONITORADO}")

# --- ESTRUTURA DE ABAS (TABS) ---
tab_monitoramento, tab_crowdsourcing = st.tabs(["📊 Monitoramento e Risco", "📢 Dashboard Cidadão"])

# =====================================================================
# ABA 1: MONITORAMENTO E RISCO
# =====================================================================
with tab_monitoramento:
    st.header("Painel Hidrometeorológico")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        nivel_rio = nivel_rio_real if nivel_rio_real else st.number_input("Nível do rio (m)", value=1.50)
        st.metric(label="Nível do Rio", value=f"{nivel_rio:.2f} m", delta="Online" if nivel_rio_real else "Offline")
    with m2: st.metric(label="Chuva Registada", value=f"{chuva_real_ana:.1f} mm" if chuva_real_ana else "Sem dados")
    with m3: st.metric(label="Previsão", value=f"{chuva_prevista_api:.1f} mm")
    with m4:
        icones_mare = {"Maré Baixa": "🌑", "Maré Descendo": "↘️", "Maré Subindo": "↗️", "Maré Alta": "🌕"}
        st.metric(label="Oceano", value=estado_mare, delta=icones_mare.get(estado_mare, ""))

    st.markdown("---")
    st.subheader("Análise Hiperlocal por Via")
    
    col_a, col_b = st.columns(2)
    with col_a: via_selecionada = st.selectbox("Selecione a área:", list(VIAS_CRITICAS.keys()))
    with col_b: terreno = st.selectbox("Terreno predominante", ["Urbano", "Rural", "Área de Várzea"], index=2)

    dados_via = VIAS_CRITICAS[via_selecionada]
    chuva_analise = st.number_input("Simulação de chuva (mm)", value=float(chuva_prevista_api if chuva_prevista_api > 0 else (chuva_real_ana if chuva_real_ana else 0.0)))

    if st.button("Executar Análise Multivariável", type="primary"):
        st.divider()
        lim_c_alto = (80.0 if terreno == "Área de Várzea" else 100.0) * dados_via["fator_reducao"]
        lim_r_alto = (4.5 if terreno == "Área de Várzea" else 6.0) * dados_via["fator_reducao"]
        lim_c_med = 50.0 * dados_via["fator_reducao"]
        lim_r_med = 3.5 * dados_via["fator_reducao"]

        # Integração: Verifica o banco de dados antes de calcular o risco
        tem_relato = verificar_relatos_recentes(via_selecionada)

        risco_final, msg_final, tipo_alerta = calcular_risco_com_mare_e_relatos(
            chuva_analise, nivel_rio, estado_mare, lim_c_alto, lim_r_alto, lim_c_med, lim_r_med, tem_relato
        )

        if tipo_alerta == "error": st.error(msg_final)
        elif tipo_alerta == "warning": st.warning(msg_final)
        else: st.success(msg_final)

        salvar_leitura(via_selecionada, chuva_analise, nivel_rio, estado_mare, risco_final)

# =====================================================================
# ABA 2: CROWDSOURCING CIDADÃO
# =====================================================================
with tab_crowdsourcing:
    st.header("📢 Reportar Alagamento")
    st.write("Colabore informando as condições reais da sua rua. Seus dados calibram a precisão do nosso sistema.")
    
    # Formulário de Relato
    with st.container(border=True):
        f_local = st.selectbox("Local Afetado:", [""] + list(VIAS_CRITICAS.keys()) + ["Outra via não listada"])
        if f_local == "Outra via não listada":
            f_local = st.text_input("Digite o nome da via:")
            
        f_nivel = st.select_slider("Nível da Água:", options=["Baixo", "Médio", "Alto"])
        f_obs = st.text_area("Observações (Opcional):", placeholder="Ex: Água entrando nas casas, bueiro entupido...")
        
        if st.button("Enviar Relato à Central"):
            if f_local.strip() == "":
                st.error("Por favor, informe o local afetado.")
            else:
                salvar_relato_cidadao(f_local, f_nivel, f_obs)
                st.success("Relato recebido com sucesso! Obrigado por colaborar com a Defesa Civil e a pesquisa local.")

    st.markdown("---")
    st.subheader("🗺️ Dashboard Colaborativo")
    
    # Leitura dos dados do banco para o Dashboard
    conn = sqlite3.connect("historico_hydroalerta_v3.db")
    df_relatos = pd.read_sql("SELECT local, nivel_alagamento, observacao, data_hora FROM relatos_alagamento ORDER BY id DESC", conn)
    conn.close()

    if not df_relatos.empty:
        df_relatos['data_hora'] = pd.to_datetime(df_relatos['data_hora'])
        ultimas_24h = df_relatos[df_relatos['data_hora'] >= (datetime.now() - timedelta(hours=24))]
        
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Total de Relatos", len(df_relatos))
        d2.metric("Relatos (Últimas 24h)", len(ultimas_24h))
        d3.metric("Região Mais Crítica", df_relatos['local'].mode()[0] if not df_relatos.empty else "N/A")
        d4.metric("Último Relato", df_relatos.iloc[0]['data_hora'].strftime("%H:%M"))

        # Filtro de Tabela
        filtro_via = st.selectbox("Filtrar relatos por local:", ["Todos"] + list(df_relatos['local'].unique()))
        
        if filtro_via != "Todos":
            st.dataframe(df_relatos[df_relatos['local'] == filtro_via], use_container_width=True)
        else:
            st.dataframe(df_relatos, use_container_width=True)
    else:
        st.info("Nenhum relato de alagamento registrado pela comunidade até o momento.")