"""
BioScan Healthspan — Testes automatizados
Execute com: pytest

Os testes cobrem:
  - Parser CSV (usando o arquivo real da Tanita como fixture)
  - API REST (login, pacientes, medições, import CSV)
  - Risk flags (classificação de risco)
"""

import io
import json
import pytest
from datetime import date

# ── Fixture: conteúdo do CSV real da Tanita ───────────────────────────────
TANITA_CSV_REAL = (
    'Date,"Weight (kg)",BMI,"Body Fat (%)","Visc Fat","Muscle Mass (kg)",'
    '"Muscle Quality","Bone Mass (kg)","BMR (kcal)","Metab Age","Body Water (%)",'
    '"Physique Rating","Muscle mass - right arm","Muscle mass - left arm",'
    '"Muscle mass - right leg","Muscle mass - left leg","Muscle mass - trunk",'
    '"Muscle quality - right arm","Muscle quality - left arm","Muscle quality - right leg",'
    '"Muscle quality - left leg","Muscle quality - trunk","Body fat (%) - right arm",'
    '"Body fat (%) - left arm","Body fat (%) - right leg","Body fat (%) - left leg",'
    '"Body fat (%) - trunk","Heart rate"\n'
    '"2026-04-14 14:19:09",107.70,34.40,30.70,18.50,70.95,48.00,3.70,2215.00,73.00,'
    '54.50,3.00,4.70,4.95,13.65,13.40,34.25,45.00,40.00,48.00,51.00,-,20.00,18.40,'
    '15.90,15.40,41.50,88.00\n'
)

TANITA_CSV_MISSING_COLS = "Date,Weight (kg)\n2026-01-01,100\n"


# ── App Flask de teste ────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def app():
    from bioscan.app import create_app
    application = create_app()
    application.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
    })
    with application.app_context():
        from bioscan.models import db
        db.create_all()
        _seed_test_data()
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def doctor_token(client):
    """Retorna JWT de um médico já autenticado."""
    resp = client.post("/bioscan/auth/login",
                       json={"email": "doctor@test.com", "password": "testpass"})
    return resp.get_json()["token"]


@pytest.fixture
def patient_token(client):
    """Retorna JWT de um paciente já autenticado."""
    resp = client.post("/bioscan/auth/login",
                       json={"email": "patient@test.com", "password": "testpass"})
    return resp.get_json()["token"]


def _seed_test_data():
    from bioscan.models import db, User, Patient
    # Médico
    doc = User(email="doctor@test.com", role="doctor", name="Dr. Teste")
    doc.set_password("testpass")
    db.session.add(doc)
    db.session.flush()
    # Paciente
    pat_record = Patient(
        name="Paciente Teste", sex="M", height_cm=178,
        birth_date=date(1988, 3, 15), created_by=doc.id
    )
    db.session.add(pat_record)
    db.session.flush()
    # User vinculado ao paciente
    pat_user = User(email="patient@test.com", role="patient",
                    name="Paciente Teste", patient_id=pat_record.id)
    pat_user.set_password("testpass")
    db.session.add(pat_user)
    db.session.commit()


# ═════════════════════════════════════════════════════════════════════════
# 1. PARSER CSV
# ═════════════════════════════════════════════════════════════════════════

class TestTanitaParser:

    def test_parse_real_csv(self):
        """Parseia o CSV real da Tanita sem erros."""
        from bioscan.tanita_parser import parse_tanita_csv
        rows = parse_tanita_csv(TANITA_CSV_REAL)
        assert len(rows) == 1

    def test_campos_numericos(self):
        """Todos os campos numéricos principais devem ser float."""
        from bioscan.tanita_parser import parse_tanita_csv
        m = parse_tanita_csv(TANITA_CSV_REAL)[0]
        assert m["weight"]   == 107.70
        assert m["bmi"]      == 34.40
        assert m["fat_pct"]  == 30.70
        assert m["visceral"] == 18.50
        assert m["muscle_kg"] == 70.95
        assert m["bone_kg"]  == 3.70
        assert m["bmr"]      == 2215.0
        assert m["meta_age"] == 73.0
        assert m["water_pct"] == 54.50

    def test_segmental_muscular(self):
        """Campos segmentais de músculo devem ser parseados corretamente."""
        from bioscan.tanita_parser import parse_tanita_csv
        m = parse_tanita_csv(TANITA_CSV_REAL)[0]
        assert m["seg_musc_right_arm"]  == 4.70
        assert m["seg_musc_left_arm"]   == 4.95
        assert m["seg_musc_right_leg"]  == 13.65
        assert m["seg_musc_left_leg"]   == 13.40
        assert m["seg_musc_trunk"]      == 34.25

    def test_segmental_gordura(self):
        """Campos segmentais de gordura devem ser parseados corretamente."""
        from bioscan.tanita_parser import parse_tanita_csv
        m = parse_tanita_csv(TANITA_CSV_REAL)[0]
        assert m["seg_fat_right_arm"]  == 20.00
        assert m["seg_fat_left_arm"]   == 18.40
        assert m["seg_fat_trunk"]      == 41.50

    def test_traco_vira_none(self):
        """O valor '-' do seg_qual_trunk deve virar None."""
        from bioscan.tanita_parser import parse_tanita_csv
        m = parse_tanita_csv(TANITA_CSV_REAL)[0]
        assert m["seg_qual_trunk"] is None

    def test_datetime_parseado(self):
        """O campo measured_at deve ser um datetime Python."""
        from bioscan.tanita_parser import parse_tanita_csv
        from datetime import datetime
        m = parse_tanita_csv(TANITA_CSV_REAL)[0]
        assert isinstance(m["measured_at"], datetime)
        assert m["measured_at"].year == 2026
        assert m["measured_at"].month == 4

    def test_csv_invalido_levanta_erro(self):
        """CSV sem colunas mínimas deve levantar ValueError com mensagem clara."""
        from bioscan.tanita_parser import parse_tanita_csv
        with pytest.raises(ValueError, match="colunas ausentes"):
            parse_tanita_csv(TANITA_CSV_MISSING_COLS)

    def test_csv_vazio_levanta_erro(self):
        """Arquivo sem medições deve levantar ValueError."""
        from bioscan.tanita_parser import parse_tanita_csv
        csv_sem_dados = (
            'Date,"Weight (kg)",BMI,"Body Fat (%)","Visc Fat","Muscle Mass (kg)",'
            '"Muscle Quality","Bone Mass (kg)","BMR (kcal)","Metab Age","Body Water (%)",'
            '"Physique Rating","Muscle mass - right arm","Muscle mass - left arm",'
            '"Muscle mass - right leg","Muscle mass - left leg","Muscle mass - trunk",'
            '"Muscle quality - right arm","Muscle quality - left arm",'
            '"Muscle quality - right leg","Muscle quality - left leg",'
            '"Muscle quality - trunk","Body fat (%) - right arm",'
            '"Body fat (%) - left arm","Body fat (%) - right leg",'
            '"Body fat (%) - left leg","Body fat (%) - trunk","Heart rate"\n'
        )
        with pytest.raises(ValueError, match="Nenhuma medição"):
            parse_tanita_csv(csv_sem_dados)

    def test_bom_utf8_ignorado(self):
        """BOM UTF-8 no início do arquivo deve ser ignorado."""
        from bioscan.tanita_parser import parse_tanita_csv
        csv_com_bom = "\ufeff" + TANITA_CSV_REAL
        rows = parse_tanita_csv(csv_com_bom.encode("utf-8-sig"))
        assert len(rows) == 1


# ═════════════════════════════════════════════════════════════════════════
# 2. AUTH
# ═════════════════════════════════════════════════════════════════════════

class TestAuth:

    def test_login_sucesso(self, client):
        resp = client.post("/bioscan/auth/login",
                           json={"email": "doctor@test.com", "password": "testpass"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data
        assert data["user"]["role"] == "doctor"

    def test_login_senha_errada(self, client):
        resp = client.post("/bioscan/auth/login",
                           json={"email": "doctor@test.com", "password": "errada"})
        assert resp.status_code == 401

    def test_login_email_inexistente(self, client):
        resp = client.post("/bioscan/auth/login",
                           json={"email": "nao@existe.com", "password": "qualquer"})
        assert resp.status_code == 401

    def test_sem_token_retorna_401(self, client):
        resp = client.get("/bioscan/patients")
        assert resp.status_code == 401

    def test_token_invalido_retorna_401(self, client):
        resp = client.get("/bioscan/patients",
                          headers={"Authorization": "Bearer token.falso.aqui"})
        assert resp.status_code == 401


# ═════════════════════════════════════════════════════════════════════════
# 3. PACIENTES
# ═════════════════════════════════════════════════════════════════════════

class TestPatients:

    def test_listar_pacientes(self, client, doctor_token):
        resp = client.get("/bioscan/patients",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_criar_paciente(self, client, doctor_token):
        resp = client.post("/bioscan/patients",
                           headers={"Authorization": f"Bearer {doctor_token}"},
                           json={
                               "name": "Novo Paciente",
                               "sex": "F",
                               "height_cm": 165,
                               "birth_date": "1990-06-20",
                               "tags": ["Perda de peso"],
                           })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Novo Paciente"
        assert data["age"] is not None

    def test_criar_paciente_sem_nome_falha(self, client, doctor_token):
        resp = client.post("/bioscan/patients",
                           headers={"Authorization": f"Bearer {doctor_token}"},
                           json={"sex": "M"})
        assert resp.status_code == 400

    def test_paciente_nao_ve_outro_paciente(self, client, patient_token):
        """Um paciente não pode ver dados de outro paciente (id=999)."""
        resp = client.get("/bioscan/patients/999",
                          headers={"Authorization": f"Bearer {patient_token}"})
        assert resp.status_code in (403, 404)

    def test_medico_ve_qualquer_paciente(self, client, doctor_token):
        resp = client.get("/bioscan/patients/1",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        assert resp.status_code == 200

    def test_paciente_nao_pode_listar_todos(self, client, patient_token):
        """Paciente não tem acesso à lista geral."""
        resp = client.get("/bioscan/patients",
                          headers={"Authorization": f"Bearer {patient_token}"})
        assert resp.status_code == 403


# ═════════════════════════════════════════════════════════════════════════
# 4. IMPORT CSV
# ═════════════════════════════════════════════════════════════════════════

class TestImportCSV:

    def test_import_csv_sucesso(self, client, doctor_token):
        data = {"csv": (io.BytesIO(TANITA_CSV_REAL.encode()), "tanita.csv")}
        resp = client.post("/bioscan/patients/1/import-csv",
                           headers={"Authorization": f"Bearer {doctor_token}"},
                           data=data, content_type="multipart/form-data")
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["inserted"] == 1
        assert len(body["measurements"]) == 1

    def test_import_csv_duplicata_ignorada(self, client, doctor_token):
        """Importar o mesmo CSV duas vezes não duplica medições."""
        data = {"csv": (io.BytesIO(TANITA_CSV_REAL.encode()), "tanita.csv")}
        # Segunda importação — deve ser ignorada
        resp = client.post("/bioscan/patients/1/import-csv",
                           headers={"Authorization": f"Bearer {doctor_token}"},
                           data=data, content_type="multipart/form-data")
        body = resp.get_json()
        assert body["inserted"] == 0
        assert body["skipped"] == 1

    def test_import_csv_invalido(self, client, doctor_token):
        data = {"csv": (io.BytesIO(b"col_errada,outra\n1,2"), "ruim.csv")}
        resp = client.post("/bioscan/patients/1/import-csv",
                           headers={"Authorization": f"Bearer {doctor_token}"},
                           data=data, content_type="multipart/form-data")
        assert resp.status_code == 422

    def test_import_sem_campo_csv(self, client, doctor_token):
        resp = client.post("/bioscan/patients/1/import-csv",
                           headers={"Authorization": f"Bearer {doctor_token}"},
                           data={}, content_type="multipart/form-data")
        assert resp.status_code == 400


# ═════════════════════════════════════════════════════════════════════════
# 5. MEDIÇÕES
# ═════════════════════════════════════════════════════════════════════════

class TestMeasurements:

    def test_listar_medicoes(self, client, doctor_token):
        resp = client.get("/bioscan/patients/1/measurements",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_campos_segmentais_presentes(self, client, doctor_token):
        """Verifica que campos segmentais estão no retorno."""
        resp = client.get("/bioscan/patients/1/measurements",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        meds = resp.get_json()
        if meds:
            m = meds[0]
            assert "seg_musc" in m
            assert "seg_fat" in m
            assert "seg_qual" in m
            assert m["seg_musc"]["right_arm"] == 4.70
            assert m["seg_fat"]["trunk"] == 41.50

    def test_seg_qual_trunk_none(self, client, doctor_token):
        """O seg_qual_trunk (que era '-' no CSV) deve ser None."""
        resp = client.get("/bioscan/patients/1/measurements",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        meds = resp.get_json()
        if meds:
            assert meds[0]["seg_qual"]["trunk"] is None


# ═════════════════════════════════════════════════════════════════════════
# 6. HEALTHSPAN SUMMARY + RISK FLAGS
# ═════════════════════════════════════════════════════════════════════════

class TestHealthspanSummary:

    def test_summary_retorna_campos(self, client, doctor_token):
        resp = client.get("/bioscan/patients/1/summary",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "latest" in data
        assert "deltas" in data
        assert "risk_flags" in data
        assert "period" in data

    def test_risk_flag_visceral_elevado(self, client, doctor_token):
        """Visceral 18.5 deve gerar flag de alert."""
        resp = client.get("/bioscan/patients/1/summary",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        flags = resp.get_json().get("risk_flags", [])
        visc_flags = [f for f in flags if f["field"] == "visceral"]
        assert len(visc_flags) >= 1
        assert visc_flags[0]["level"] == "alert"

    def test_risk_flag_bmi_obesidade(self, client, doctor_token):
        """IMC 34.4 deve gerar flag de alert."""
        resp = client.get("/bioscan/patients/1/summary",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        flags = resp.get_json().get("risk_flags", [])
        bmi_flags = [f for f in flags if f["field"] == "bmi"]
        assert len(bmi_flags) >= 1
        assert bmi_flags[0]["level"] == "alert"

    def test_risk_flag_idade_metabolica(self, client, doctor_token):
        """Idade metabólica 73 anos com paciente de 38 deve gerar alert."""
        resp = client.get("/bioscan/patients/1/summary",
                          headers={"Authorization": f"Bearer {doctor_token}"})
        flags = resp.get_json().get("risk_flags", [])
        meta_flags = [f for f in flags if f["field"] == "meta_age"]
        assert len(meta_flags) >= 1
        assert meta_flags[0]["level"] == "alert"

    def test_health_endpoint(self, client):
        """Healthcheck do Railway deve retornar 200."""
        resp = client.get("/bioscan/health")
        assert resp.status_code == 200
