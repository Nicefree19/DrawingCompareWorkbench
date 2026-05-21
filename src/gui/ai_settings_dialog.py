# -*- coding: utf-8 -*-
"""Phase J Step 3 (J1) — AI classifier settings dialog.

Lets the user pick the embedding mode (Auto / Quality / Speed / Off),
adjust the cosine threshold, and choose a Matryoshka truncation
target — all without editing JSON or restarting the workbench.

UX policy:
  * Modal QDialog opened from `[설정] → [🤖 AI 분류기 설정...]`
  * On `OK`: writes ai_config.json (atomic via config_io.save_ai_config)
    + clears the dispatcher singleton cache so the next zone
    classification picks up the new mode + triggers a fresh
    background warmup so the user sees the new state in lbl_status_v2
    without needing to restart the workbench.
  * On `Cancel`: no changes persisted, no cache cleared.
  * `[테스트 인코드]` button runs a 1-zone synthetic classify so the
    user can verify the new backend works before saving.

Status indicators:
  * Each backend ID gets a colored dot next to it indicating
    probe_available() result — green ✓ if the model is on disk +
    deps installed, red ✗ otherwise. Computed on dialog open.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpacerItem,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Display labels for each backend mode. Order = display order in combo.
_MODE_OPTIONS: list[tuple[str, str, str]] = [
    # (combo_value, display_label, tooltip)
    (
        "auto",
        "🤖 자동 (권장) — 사용 가능한 모델 자동 선택",
        "Qwen GGUF가 있으면 Quality, 없으면 mxbai ONNX, 둘 다 없으면 휴리스틱만",
    ),
    (
        "llama_cpp_qwen3_embedding",
        "💎 Quality — Qwen3-Embedding (느린 cold-start, 최고 정확도)",
        "639 MB GGUF 필요. cold-start 2-5초, 한국어 STS 우수",
    ),
    (
        "onnx_mxbai_large",
        "⚡ Speed — mxbai-embed-large ONNX (빠른 cold-start)",
        "670 MB ONNX 필요. cold-start 200-300ms, 정확도는 Quality 대비 약간 ↓",
    ),
    (
        "__off__",
        "🚫 Off — AI 분류 끄기 (휴리스틱 only)",
        "Stage-2 임베딩 분류 비활성화. Stage-1 keyword 휴리스틱만 사용.",
    ),
]

# Display labels for output_dim. Order = display order in combo.
# None means "use backend's native dim". Quality/Auto modes typically
# use None; Speed mode defaults to 512 (Matryoshka).
_OUTPUT_DIM_OPTIONS: list[tuple[Optional[int], str]] = [
    (None, "원본 차원 (전체)"),
    (1024, "1024"),
    (768, "768"),
    (512, "512 (Matryoshka, Speed mode 기본)"),
    (256, "256"),
    (128, "128"),
]


# Phase L1 — LLM cascade (Stage-3) options for the dialog.
# (combo_value, display_label, tooltip)
_LLM_BACKEND_OPTIONS: list[tuple[str, str, str]] = [
    (
        "stub_llm",
        "🧪 Stub (테스트 / 개발용)",
        "결정론적 stub — 첫 후보 자동 선택. Ollama 없이도 cascade 동작 확인용. "
        "정확도 낮음.",
    ),
    (
        "ollama_exaone",
        "🦙 Ollama EXAONE-3.5 (실제 LLM)",
        "localhost:11434 Ollama 서버 + `ollama pull exaone3.5:7.8b` 필요. "
        "한국어 최적화, zone당 1-3초.",
    ),
]


# Phase L3 — KDS RAG client options for the dialog.
# (combo_value, display_label, tooltip)
_KDS_RAG_CLIENT_OPTIONS: list[tuple[str, str, str]] = [
    (
        "stub_kds",
        "🧪 Stub (RAG 비활성)",
        "항상 빈 컨텍스트 반환. KDS 조항 자동 주입 없이 LLM만 동작.",
    ),
    (
        "local_json_kds",
        "📚 Local JSON (오프라인)",
        "%LOCALAPPDATA%/DrawingCompareWorkbench/kds_clauses.json 파일에서 "
        "키워드 + 카테고리 매칭으로 조항 검색. 오프라인 동작, 의존성 없음.",
    ),
]


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class AiSettingsDialog(QDialog):
    """AI classifier 설정 다이얼로그.

    External contract:
      * Caller constructs with the currently-active config
      * Calls .exec() to show modal
      * After exec returns Accepted: caller should re-load ai_config.
        json (the dialog already wrote it) AND clear the dispatcher
        singleton cache + re-trigger prepare_async.

    The dialog itself does NOT touch the dispatcher — the workbench
    owns that lifecycle. We only persist the config file and signal
    via .result().
    """

    def __init__(self, current_config, parent=None) -> None:
        super().__init__(parent)
        self._current_config = current_config
        self.setWindowTitle("AI 분류기 설정")
        self.setMinimumWidth(560)
        self._build_ui()
        self._populate_from_config(current_config)
        self._refresh_probe_indicators()

    # ---- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 16)

        # Header — explains what this dialog does
        header = QLabel(
            "<b>AI 분류기 모드 + 임계값</b><br>"
            "<span style='color:#888;'>변경 사항은 즉시 저장되어 다음 zone 분류부터 적용됩니다.</span>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        # Phase M5 review note: cascade dependency banner. The dialog
        # has 3 sections (Embedding / LLM / KDS RAG) that gate each
        # other (Off → LLM Off → RAG Off). Without this banner, users
        # who see LLM/RAG widgets greyed out have no idea why — they
        # blame the app for "broken UI". Three lines of HTML eliminate
        # the most common support question.
        cascade_note = QLabel(
            "<span style='color:#666; font-size:11px;'>"
            "💡 <b>3단계 캐스케이드</b>: "
            "임베딩 모드 → LLM 캐스케이드 → KDS RAG 순서로 활성화 가능. "
            "각 단계는 위 단계가 켜져 있어야 동작합니다."
            "</span>"
        )
        cascade_note.setTextFormat(Qt.TextFormat.RichText)
        cascade_note.setWordWrap(True)
        cascade_note.setStyleSheet(
            "background-color: #f0f4f8; "
            "border-left: 3px solid #4a90e2; "
            "padding: 6px 10px; "
            "border-radius: 3px;"
        )
        layout.addWidget(cascade_note)

        layout.addWidget(_hr())

        # ----- Mode selection -----
        form = QFormLayout()
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        self.cmb_mode = QComboBox()
        for value, label, tooltip in _MODE_OPTIONS:
            self.cmb_mode.addItem(label, userData=value)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("모드:", self.cmb_mode)

        # Probe-status indicator next to the combo
        self.lbl_probe_status = QLabel("")
        self.lbl_probe_status.setTextFormat(Qt.TextFormat.RichText)
        form.addRow("모델 상태:", self.lbl_probe_status)

        # ----- Cosine threshold -----
        self.spn_threshold = QDoubleSpinBox()
        self.spn_threshold.setRange(0.30, 0.99)
        self.spn_threshold.setSingleStep(0.05)
        self.spn_threshold.setDecimals(2)
        self.spn_threshold.setToolTip(
            "임베딩 분류기가 자신 있게 답하기 위한 최소 cosine 유사도. "
            "낮추면 더 많은 zone이 임베딩으로 분류되지만 오분류도 늘어남."
        )
        form.addRow("Cosine 임계값:", self.spn_threshold)

        # ----- Matryoshka output dim -----
        self.cmb_output_dim = QComboBox()
        for value, label in _OUTPUT_DIM_OPTIONS:
            self.cmb_output_dim.addItem(label, userData=value)
        self.cmb_output_dim.setToolTip(
            "차원을 줄이면 메모리/속도 ↑ 하지만 미세한 정확도 손실 가능. "
            "Speed 모드에서는 512 권장 (Matryoshka)."
        )
        form.addRow("출력 차원:", self.cmb_output_dim)

        layout.addLayout(form)

        layout.addWidget(_hr())

        # ----- Phase L1 — Stage-3 LLM cascade -----
        llm_header = QLabel(
            "<b>Stage-3 LLM (모호한 zone만)</b><br>"
            "<span style='color:#888;'>Stage-2 임베딩 confidence가 임계값 미만일 때만 LLM 호출. "
            "신뢰도 충분한 zone은 LLM 건너뜀 → 비용 ↓.</span>"
        )
        llm_header.setTextFormat(Qt.TextFormat.RichText)
        llm_header.setWordWrap(True)
        layout.addWidget(llm_header)

        llm_form = QFormLayout()
        llm_form.setSpacing(8)
        llm_form.setContentsMargins(0, 4, 0, 0)

        # Enable / disable
        self.chk_use_llm = QCheckBox("LLM 캐스케이드 사용 (Stage-3)")
        self.chk_use_llm.setToolTip(
            "켜면 Stage-2 결과가 모호할 때 LLM이 정밀 분류를 시도합니다. "
            "끄면 Stage-2 임베딩 결과만 사용."
        )
        self.chk_use_llm.toggled.connect(self._on_llm_toggled)
        llm_form.addRow("", self.chk_use_llm)

        # Backend selector
        self.cmb_llm_backend = QComboBox()
        for value, label, tooltip in _LLM_BACKEND_OPTIONS:
            self.cmb_llm_backend.addItem(label, userData=value)
        self.cmb_llm_backend.currentIndexChanged.connect(
            self._refresh_llm_probe_indicator
        )
        # Phase L4: backend-change must also gate the host + model
        # widgets (only meaningful for Ollama, not Stub)
        self.cmb_llm_backend.currentIndexChanged.connect(
            lambda _idx: self._refresh_ollama_endpoint_widget_state()
        )
        llm_form.addRow("LLM 백엔드:", self.cmb_llm_backend)

        # LLM backend probe status
        self.lbl_llm_probe_status = QLabel("")
        self.lbl_llm_probe_status.setTextFormat(Qt.TextFormat.RichText)
        llm_form.addRow("백엔드 상태:", self.lbl_llm_probe_status)

        # Phase L4 (Issue #6 fix): Ollama endpoint widgets so users
        # can persist host + model for non-localhost / non-default
        # deployments. Without these, custom Ollama setups had to be
        # re-applied via JSON edit after every Workbench restart.
        self.txt_llm_host = QLineEdit()
        self.txt_llm_host.setPlaceholderText("http://localhost:11434")
        self.txt_llm_host.setToolTip(
            "Ollama 서버의 base URL. 원격 Ollama 사용 시 변경.\n"
            "예: http://10.0.0.5:11434, https://ollama.internal\n"
            "Stub 백엔드 선택 시에는 사용 안 됨."
        )
        # Re-probe whenever the user finishes editing the host
        self.txt_llm_host.editingFinished.connect(
            self._refresh_llm_probe_indicator
        )
        llm_form.addRow("Ollama 호스트:", self.txt_llm_host)

        self.txt_llm_model = QLineEdit()
        self.txt_llm_model.setPlaceholderText("exaone3.5:7.8b")
        self.txt_llm_model.setToolTip(
            "Ollama 모델 이름 (예: exaone3.5:7.8b, llama3.2:3b).\n"
            "사전에 `ollama pull <모델>` 로 다운로드되어 있어야 함.\n"
            "Stub 백엔드 선택 시에는 사용 안 됨."
        )
        self.txt_llm_model.editingFinished.connect(
            self._refresh_llm_probe_indicator
        )
        llm_form.addRow("Ollama 모델:", self.txt_llm_model)

        # Invoke threshold (Stage-2 confidence below this triggers LLM)
        self.spn_llm_invoke_threshold = QDoubleSpinBox()
        self.spn_llm_invoke_threshold.setRange(0.30, 0.99)
        self.spn_llm_invoke_threshold.setSingleStep(0.05)
        self.spn_llm_invoke_threshold.setDecimals(2)
        self.spn_llm_invoke_threshold.setToolTip(
            "Stage-2 confidence가 이 값 미만일 때만 LLM 호출. "
            "0.85 (기본) → 신뢰도 ≥ 85%인 zone은 LLM 건너뜀. "
            "낮추면 LLM 호출 더 늘어남 (정확도 ↑, 속도 ↓)."
        )
        llm_form.addRow("LLM 호출 임계값:", self.spn_llm_invoke_threshold)

        # Top-K candidates
        self.spn_llm_top_k = QSpinBox()
        self.spn_llm_top_k.setRange(1, 8)
        self.spn_llm_top_k.setToolTip(
            "Stage-2의 상위 K개 후보 카테고리만 LLM에 전달. "
            "K가 크면 LLM이 더 자유롭게 선택 (정확도 ↑) 하지만 prompt 길이 ↑."
        )
        llm_form.addRow("후보 카테고리 수 (Top-K):", self.spn_llm_top_k)

        # Timeout
        self.spn_llm_timeout = QDoubleSpinBox()
        self.spn_llm_timeout.setRange(1.0, 300.0)
        self.spn_llm_timeout.setSingleStep(1.0)
        self.spn_llm_timeout.setDecimals(1)
        self.spn_llm_timeout.setSuffix(" s")
        self.spn_llm_timeout.setToolTip(
            "LLM 호출당 최대 대기 시간. 초과 시 abstain → Stage-2 결과 유지."
        )
        llm_form.addRow("타임아웃:", self.spn_llm_timeout)

        layout.addLayout(llm_form)

        layout.addWidget(_hr())

        # ----- Phase L3 — KDS RAG (Stage-3 보강) -----
        rag_header = QLabel(
            "<b>KDS RAG (Stage-3 LLM 보강)</b><br>"
            "<span style='color:#888;'>한국 건설기준 (KDS / KCS) 조항 텍스트를 LLM 프롬프트에 자동 주입. "
            "LLM이 카테고리 선택 시 표준 조항을 인용 가능 → 검토자가 근거 추적.</span>"
        )
        rag_header.setTextFormat(Qt.TextFormat.RichText)
        rag_header.setWordWrap(True)
        layout.addWidget(rag_header)

        rag_form = QFormLayout()
        rag_form.setSpacing(8)
        rag_form.setContentsMargins(0, 4, 0, 0)

        # Enable / disable
        self.chk_use_kds_rag = QCheckBox("KDS RAG 사용 (LLM 프롬프트에 조항 주입)")
        self.chk_use_kds_rag.setToolTip(
            "켜면 LLM 호출 직전에 zone 텍스트와 관련된 KDS 조항을 검색해 "
            "프롬프트에 추가합니다. LLM이 표준 조항을 인용 가능. "
            "LLM 캐스케이드가 꺼져 있으면 무시됨."
        )
        self.chk_use_kds_rag.toggled.connect(self._on_kds_rag_toggled)
        rag_form.addRow("", self.chk_use_kds_rag)

        # Client selector
        self.cmb_kds_rag_client = QComboBox()
        for value, label, tooltip in _KDS_RAG_CLIENT_OPTIONS:
            self.cmb_kds_rag_client.addItem(label, userData=value)
        self.cmb_kds_rag_client.currentIndexChanged.connect(
            self._refresh_kds_rag_probe_indicator
        )
        rag_form.addRow("RAG 클라이언트:", self.cmb_kds_rag_client)

        # Probe status
        self.lbl_kds_rag_probe_status = QLabel("")
        self.lbl_kds_rag_probe_status.setTextFormat(Qt.TextFormat.RichText)
        rag_form.addRow("클라이언트 상태:", self.lbl_kds_rag_probe_status)

        # Top-K clauses
        self.spn_kds_rag_top_k = QSpinBox()
        self.spn_kds_rag_top_k.setRange(1, 10)
        self.spn_kds_rag_top_k.setToolTip(
            "검색된 조항 중 상위 K개를 LLM 프롬프트에 주입. "
            "K가 크면 컨텍스트 풍부하지만 prompt 길이 ↑."
        )
        rag_form.addRow("주입 조항 수 (Top-K):", self.spn_kds_rag_top_k)

        # Timeout
        self.spn_kds_rag_timeout = QDoubleSpinBox()
        self.spn_kds_rag_timeout.setRange(0.5, 60.0)
        self.spn_kds_rag_timeout.setSingleStep(0.5)
        self.spn_kds_rag_timeout.setDecimals(1)
        self.spn_kds_rag_timeout.setSuffix(" s")
        self.spn_kds_rag_timeout.setToolTip(
            "RAG 검색당 최대 대기 시간. 초과 시 빈 컨텍스트 → LLM은 RAG "
            "없이 분류 진행."
        )
        rag_form.addRow("RAG 타임아웃:", self.spn_kds_rag_timeout)

        layout.addLayout(rag_form)

        layout.addWidget(_hr())

        # ----- Test encode button -----
        test_row = QHBoxLayout()
        self.btn_test_encode = QPushButton("🧪 테스트 인코드 실행")
        self.btn_test_encode.setToolTip(
            "현재 선택한 모드로 단일 zone 분류를 시도해 결과를 표시합니다. "
            "OK 누르기 전에 백엔드가 정상 작동하는지 확인할 때 사용."
        )
        self.btn_test_encode.clicked.connect(self._on_test_encode)
        test_row.addWidget(self.btn_test_encode)
        test_row.addStretch(1)
        layout.addLayout(test_row)

        self.lbl_test_result = QLabel("")
        self.lbl_test_result.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_test_result.setWordWrap(True)
        layout.addWidget(self.lbl_test_result)

        layout.addItem(QSpacerItem(0, 8, QSizePolicy.Policy.Minimum,
                                    QSizePolicy.Policy.Expanding))

        # ----- OK / Cancel -----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---- Population + slots --------------------------------------------

    def _populate_from_config(self, cfg) -> None:
        """Set widget values from the incoming config snapshot."""

        # Mode resolution: when use_embedding=False, show "Off" regardless
        # of backend_id; otherwise pick the matching backend_id row.
        if not getattr(cfg, "use_embedding", True):
            target = "__off__"
        else:
            target = getattr(cfg, "embedding_backend_id", "auto")
        idx = self.cmb_mode.findData(target)
        if idx >= 0:
            self.cmb_mode.setCurrentIndex(idx)

        # Threshold
        self.spn_threshold.setValue(
            float(getattr(cfg, "embedding_threshold", 0.7))
        )

        # Output dim
        out_dim = getattr(cfg, "embedding_output_dim", None)
        idx = self.cmb_output_dim.findData(out_dim)
        if idx >= 0:
            self.cmb_output_dim.setCurrentIndex(idx)
        else:
            # Unknown value — pick "원본 차원" (None)
            self.cmb_output_dim.setCurrentIndex(0)

        # ---- Phase L1 — LLM cascade (Stage-3) ----
        self.chk_use_llm.setChecked(bool(getattr(cfg, "use_llm", False)))
        llm_bid = getattr(cfg, "llm_backend_id", "stub_llm") or "stub_llm"
        idx = self.cmb_llm_backend.findData(llm_bid)
        if idx >= 0:
            self.cmb_llm_backend.setCurrentIndex(idx)
        else:
            # Unknown backend — pick stub as safe default
            self.cmb_llm_backend.setCurrentIndex(0)
        # Phase L4 (Issue #6 fix): host + model fields
        self.txt_llm_host.setText(
            str(getattr(cfg, "llm_host", "") or "http://localhost:11434")
        )
        self.txt_llm_model.setText(
            str(getattr(cfg, "llm_model", "") or "exaone3.5:7.8b")
        )
        self.spn_llm_invoke_threshold.setValue(
            float(getattr(cfg, "llm_invoke_below_confidence", 0.85))
        )
        self.spn_llm_top_k.setValue(
            int(getattr(cfg, "llm_top_k_candidates", 3))
        )
        self.spn_llm_timeout.setValue(
            float(getattr(cfg, "llm_timeout_s", 10.0))
        )
        # Apply enable-state to dependent widgets
        self._on_llm_toggled(self.chk_use_llm.isChecked())

        # ---- Phase L3 — KDS RAG ----
        self.chk_use_kds_rag.setChecked(
            bool(getattr(cfg, "use_kds_rag", False))
        )
        rag_cid = getattr(cfg, "kds_rag_client_id", "stub_kds") or "stub_kds"
        idx = self.cmb_kds_rag_client.findData(rag_cid)
        if idx >= 0:
            self.cmb_kds_rag_client.setCurrentIndex(idx)
        else:
            self.cmb_kds_rag_client.setCurrentIndex(0)
        self.spn_kds_rag_top_k.setValue(
            int(getattr(cfg, "kds_rag_top_k", 3))
        )
        self.spn_kds_rag_timeout.setValue(
            float(getattr(cfg, "kds_rag_timeout_s", 5.0))
        )
        # Apply enable-state to dependent RAG widgets
        self._on_kds_rag_toggled(self.chk_use_kds_rag.isChecked())

    def _on_mode_changed(self, _idx: int) -> None:
        """When mode changes, refresh probe status indicator + adjust
        UI hints."""
        # Disable threshold + output_dim when "Off" mode
        is_off = self.cmb_mode.currentData() == "__off__"
        self.spn_threshold.setEnabled(not is_off)
        self.cmb_output_dim.setEnabled(not is_off)
        self.btn_test_encode.setEnabled(not is_off)
        self._refresh_probe_indicators()
        # When embedding mode is Off, LLM cascade has no Stage-2 to
        # gate against — disable the LLM section entirely so users
        # don't think they can run LLM-only mode (Stage-1 → Stage-3
        # is theoretically possible but Phase L2+ work).
        self.chk_use_llm.setEnabled(not is_off)
        if is_off:
            self.chk_use_llm.setChecked(False)
        self._on_llm_toggled(self.chk_use_llm.isChecked())

    def _on_llm_toggled(self, checked: bool) -> None:
        """When LLM cascade toggles, gate the LLM detail widgets +
        the KDS RAG section (RAG only fires inside the LLM stage)."""
        self.cmb_llm_backend.setEnabled(checked)
        self.spn_llm_invoke_threshold.setEnabled(checked)
        self.spn_llm_top_k.setEnabled(checked)
        self.spn_llm_timeout.setEnabled(checked)
        # Phase L4: host + model widgets gated on use_llm AND
        # on Ollama backend selection (stub_llm doesn't need them)
        self._refresh_ollama_endpoint_widget_state()
        if checked:
            self._refresh_llm_probe_indicator()
        else:
            self.lbl_llm_probe_status.setText(
                "<span style='color:#888;'>(LLM 캐스케이드 비활성화)</span>"
            )
        # Phase L3 — KDS RAG only useful when LLM is on; gate the
        # whole RAG section to make this dependency obvious to the user
        if hasattr(self, "chk_use_kds_rag"):
            self.chk_use_kds_rag.setEnabled(checked)
            if not checked:
                self.chk_use_kds_rag.setChecked(False)
            # Force-refresh the RAG widgets even when checked stays
            # the same (probe indicator might need update)
            self._on_kds_rag_toggled(self.chk_use_kds_rag.isChecked())

    def _refresh_ollama_endpoint_widget_state(self) -> None:
        """Phase L4: enable host + model widgets only when LLM is
        on AND Ollama backend is selected. Stub backend ignores
        these fields, so showing them enabled would confuse users
        ("why doesn't my host change anything?")."""
        if not hasattr(self, "txt_llm_host"):
            return  # _build_ui not yet finished
        llm_on = self.chk_use_llm.isChecked()
        is_ollama = self.cmb_llm_backend.currentData() == "ollama_exaone"
        enabled = llm_on and is_ollama
        self.txt_llm_host.setEnabled(enabled)
        self.txt_llm_model.setEnabled(enabled)

    # ---- Phase L3 — KDS RAG widgets ------------------------------------

    def _on_kds_rag_toggled(self, checked: bool) -> None:
        """Gate KDS RAG detail widgets on the use_kds_rag checkbox."""
        self.cmb_kds_rag_client.setEnabled(checked)
        self.spn_kds_rag_top_k.setEnabled(checked)
        self.spn_kds_rag_timeout.setEnabled(checked)
        if checked:
            self._refresh_kds_rag_probe_indicator()
        else:
            self.lbl_kds_rag_probe_status.setText(
                "<span style='color:#888;'>(RAG 비활성화)</span>"
            )

    def _refresh_kds_rag_probe_indicator(self) -> None:
        """Show whether the chosen KDS RAG client has data available."""
        cid = self.cmb_kds_rag_client.currentData()
        if not cid:
            self.lbl_kds_rag_probe_status.setText("")
            return
        ok = self._probe_kds_rag_one(cid)
        if ok:
            if cid == "stub_kds":
                self.lbl_kds_rag_probe_status.setText(
                    "<span style='color:#888;'>● Stub — RAG 사실상 비활성 "
                    "(LLM은 RAG 없이 분류)</span>"
                )
            else:
                self.lbl_kds_rag_probe_status.setText(
                    "<span style='color:#0a0;'>● 사용 가능 (kds_clauses.json 발견)</span>"
                )
        else:
            self.lbl_kds_rag_probe_status.setText(
                "<span style='color:#a00;'>● 클라이언트 미준비 — "
                "%LOCALAPPDATA%/DrawingCompareWorkbench/kds_clauses.json 파일이 "
                "필요합니다. 이 모드를 저장해도 LLM은 RAG 없이 분류 진행 (안전).</span>"
            )

    @staticmethod
    def _probe_kds_rag_one(client_id: str) -> bool:
        """KDS RAG-side probe (mirrors _probe_one + _probe_llm_one)."""
        from src.services.comparison.ai_classifier.kds_rag import (
            KDS_RAG_REGISTRY,
        )
        try:
            factory = KDS_RAG_REGISTRY.get(client_id)
            if factory is None:
                return False
            return factory().__class__.probe_available()
        except Exception:
            return False

    def _refresh_llm_probe_indicator(self) -> None:
        """Show whether the chosen LLM backend is reachable.

        Phase L3 review fix: now uses the instance-aware
        ``_probe_llm_one_with_config`` so the indicator reflects the
        user's configured host/model, not the registry default."""
        bid = self.cmb_llm_backend.currentData()
        if not bid:
            self.lbl_llm_probe_status.setText("")
            return
        ok = self._probe_llm_one_with_config(bid)
        if ok:
            if bid == "stub_llm":
                self.lbl_llm_probe_status.setText(
                    "<span style='color:#0a0;'>● 사용 가능 (stub — 항상 동작)</span>"
                )
            else:
                self.lbl_llm_probe_status.setText(
                    "<span style='color:#0a0;'>● 사용 가능 (Ollama 응답 + 모델 확인됨)</span>"
                )
        else:
            self.lbl_llm_probe_status.setText(
                "<span style='color:#a00;'>● 미설치 — Ollama 서버 미실행 또는 모델 미pull. "
                "이 모드를 저장하면 LLM 호출 시 abstain 후 Stage-2 결과 유지.</span>"
            )

    @staticmethod
    def _probe_llm_one(
        backend_id: str,
        host: str = "",
        model_name: str = "",
    ) -> bool:
        """LLM-side probe (mirrors _probe_one for embedding side).

        Phase L3 review fix: was a @staticmethod that called the
        classmethod ``probe_available()`` without arguments — meaning
        Ollama probe always hit DEFAULT_HOST regardless of the user's
        ``llm_host`` config. Now still a @staticmethod (preserves
        existing static-call usages in tests) but accepts optional
        host/model parameters that the dialog's instance methods
        thread from the current config. When called without host/
        model (the static-test path), defaults to the registry-
        default endpoint — which is the same behaviour as before
        the fix for non-Ollama backends.
        """
        from src.services.comparison.ai_classifier.llm_backends import (
            LLM_BACKEND_REGISTRY,
        )
        try:
            factory = LLM_BACKEND_REGISTRY.get(backend_id)
            if factory is None:
                return False
            cls = factory().__class__
            # Ollama probe honours custom host + model when supplied
            if backend_id == "ollama_exaone" and (host or model_name):
                from src.services.comparison.ai_classifier.llm_backends.ollama_exaone import (  # noqa: E501
                    DEFAULT_HOST, DEFAULT_MODEL,
                )
                return cls.probe_available(
                    host=host or DEFAULT_HOST,
                    model_name=model_name or DEFAULT_MODEL,
                )
            return cls.probe_available()
        except Exception:
            return False

    def _probe_llm_one_with_config(self, backend_id: str) -> bool:
        """Instance-aware wrapper: pulls host + model from
        ``self._current_config`` so the dialog's probe indicator
        matches what the dispatcher will actually use at runtime.

        Use this from dialog widgets; use ``_probe_llm_one`` for
        registry-default probes (e.g. test code or auto-mode
        bootstrap that doesn't yet have a config).
        """
        host = getattr(self._current_config, "llm_host", "")
        model = getattr(self._current_config, "llm_model", "")
        return self._probe_llm_one(backend_id, host=host, model_name=model)

    def _refresh_probe_indicators(self) -> None:
        """Show whether the chosen backend is actually available
        (probe_available() = file + dep check)."""
        from src.services.comparison.ai_classifier.backends import (
            BACKEND_REGISTRY,
        )

        bid = self.cmb_mode.currentData()
        if bid == "__off__":
            self.lbl_probe_status.setText(
                "<span style='color:#888;'>(임베딩 비활성화)</span>"
            )
            return
        if bid == "auto":
            # Show status of all candidates
            lines = []
            for cand_id in ("llama_cpp_qwen3_embedding", "onnx_mxbai_large"):
                ok = self._probe_one(cand_id)
                tag = (
                    "<span style='color:#0a0;'>사용 가능</span>"
                    if ok
                    else "<span style='color:#b45309;'>모델 없음 - 휴리스틱 분류만 사용</span>"
                )
                lines.append(f"{cand_id}: {tag}")
            self.lbl_probe_status.setText("<br>".join(lines))
            return
        ok = self._probe_one(bid)
        if ok:
            self.lbl_probe_status.setText(
                "<span style='color:#0a0;'>사용 가능 (모델 + 의존성 확인)</span>"
            )
        else:
            self.lbl_probe_status.setText(
                "<span style='color:#b45309;'>모델 없음 - 휴리스틱 분류만 사용합니다. "
                "모델 파일 또는 의존성 패키지를 설치하면 임베딩 AI 분류가 활성화됩니다.</span>"
            )

    @staticmethod
    def _probe_one(backend_id: str) -> bool:
        """probe_available 호출 — exception 시 False."""
        from src.services.comparison.ai_classifier.backends import (
            BACKEND_REGISTRY,
        )
        try:
            factory = BACKEND_REGISTRY.get(backend_id)
            if factory is None:
                return False
            return factory().__class__.probe_available()
        except Exception:
            return False

    def _on_test_encode(self) -> None:
        """Best-effort 1-zone classify test using the currently
        SELECTED settings (not yet saved). Used to confirm the user's
        choice before clicking OK.

        Runs synchronously — for a real backend the cold-start may
        block the UI for a few seconds. Test is gated on Off mode.
        """

        try:
            cfg = self._build_config_from_ui()
        except ValueError as exc:
            self.lbl_test_result.setText(
                f"<span style='color:#a00;'>설정 오류: {exc}</span>"
            )
            return
        if not cfg.use_embedding:
            self.lbl_test_result.setText(
                "<span style='color:#888;'>(Off 모드 — 테스트 인코드 비활성)</span>"
            )
            return

        # Use a tiny synthetic zone — covers the typical structural-member
        # phrasing the dispatcher's normalizer/canonicaliser handles.
        sample_zone = {
            "zone_id": "test_dialog_zone",
            "text_snippet": "보 단면 H400×200×8×13 변경",
            "layer": "BEAM",
            "change_type": "modified",
        }
        self.lbl_test_result.setText(
            "<span style='color:#888;'>테스트 인코드 진행 중… (cold-start 시 5-10초)</span>"
        )
        # Force UI repaint before the synchronous classify_zones call
        # blocks the event loop.
        QTimer.singleShot(0, lambda: self._run_test_encode(cfg, sample_zone))

    def _run_test_encode(self, cfg, sample_zone) -> None:
        try:
            from src.services.comparison.ai_classifier import classify_zones
            results = classify_zones([sample_zone], config=cfg)
        except Exception as exc:  # noqa: BLE001
            logger.exception("test_encode crashed")
            self.lbl_test_result.setText(
                f"<span style='color:#a00;'>테스트 인코드 실패: {exc}</span>"
            )
            return
        if not results:
            self.lbl_test_result.setText(
                "<span style='color:#a00;'>결과 없음</span>"
            )
            return
        r = results[0]
        backend_used = (r.classifier_used or "?")
        category = r.category.value if hasattr(r.category, "value") else str(r.category)
        self.lbl_test_result.setText(
            f"<span style='color:#0a0;'>✓ 결과: <b>{category}</b> "
            f"(classifier={backend_used}, 신뢰도={r.confidence:.2f}, "
            f"{r.elapsed_ms:.0f}ms)</span>"
        )

    # ---- Accept ---------------------------------------------------------

    def _build_config_from_ui(self):
        """Construct an AiClassifierConfig matching the current widget
        state. Caller (accept handler) persists this via save_ai_config.

        Phase L1 — also reads the LLM cascade widgets and threads the
        values into the AiClassifierConfig constructor.
        """

        from src.services.comparison.ai_classifier import AiClassifierConfig

        # ---- Resolve LLM fields (common to both branches) ----
        use_llm = bool(self.chk_use_llm.isChecked())
        llm_backend_id = (
            self.cmb_llm_backend.currentData() or "stub_llm"
        )
        llm_invoke_below = float(self.spn_llm_invoke_threshold.value())
        llm_top_k = int(self.spn_llm_top_k.value())
        llm_timeout = float(self.spn_llm_timeout.value())
        # Phase L4 (Issue #6 fix): host + model fields. Empty input
        # falls back to defaults so blank widgets don't break the
        # validator (which requires non-empty strings).
        llm_host = self.txt_llm_host.text().strip() or "http://localhost:11434"
        llm_model = self.txt_llm_model.text().strip() or "exaone3.5:7.8b"

        # ---- Phase L3 — KDS RAG fields ----
        use_kds_rag = bool(self.chk_use_kds_rag.isChecked())
        kds_rag_client_id = (
            self.cmb_kds_rag_client.currentData() or "stub_kds"
        )
        kds_rag_top_k = int(self.spn_kds_rag_top_k.value())
        kds_rag_timeout = float(self.spn_kds_rag_timeout.value())

        bid = self.cmb_mode.currentData()
        if bid == "__off__":
            # User wants embedding disabled — preserve previous
            # backend_id so re-enabling later restores their last
            # choice. LLM cascade is also forced off because
            # Stage-3 needs Stage-2 candidates to gate against
            # (Stage-1-only → Stage-3 is Phase L2+ work). KDS RAG
            # is forced off because it only runs as part of LLM.
            return AiClassifierConfig(
                enabled=True,
                use_embedding=False,
                use_llm=False,
                embedding_backend_id=getattr(
                    self._current_config, "embedding_backend_id", "auto",
                ),
                embedding_threshold=float(self.spn_threshold.value()),
                embedding_output_dim=self.cmb_output_dim.currentData(),
                llm_backend_id=llm_backend_id,  # remembered for re-enable
                llm_host=llm_host,  # remembered for re-enable (L4)
                llm_model=llm_model,  # remembered for re-enable (L4)
                llm_invoke_below_confidence=llm_invoke_below,
                llm_top_k_candidates=llm_top_k,
                llm_timeout_s=llm_timeout,
                # KDS RAG forced off (LLM is off → RAG has no LLM to feed)
                use_kds_rag=False,
                kds_rag_client_id=kds_rag_client_id,  # remembered
                kds_rag_top_k=kds_rag_top_k,
                kds_rag_timeout_s=kds_rag_timeout,
            )
        return AiClassifierConfig(
            enabled=True,
            use_embedding=True,
            use_llm=use_llm,
            embedding_backend_id=str(bid),
            embedding_threshold=float(self.spn_threshold.value()),
            embedding_output_dim=self.cmb_output_dim.currentData(),
            llm_backend_id=llm_backend_id,
            llm_host=llm_host,  # Phase L4 (Issue #6 fix)
            llm_model=llm_model,  # Phase L4 (Issue #6 fix)
            llm_invoke_below_confidence=llm_invoke_below,
            llm_top_k_candidates=llm_top_k,
            llm_timeout_s=llm_timeout,
            # KDS RAG only effective when LLM is on; auto-disable when off
            use_kds_rag=bool(use_kds_rag and use_llm),
            kds_rag_client_id=kds_rag_client_id,
            kds_rag_top_k=kds_rag_top_k,
            kds_rag_timeout_s=kds_rag_timeout,
        )

    def _on_accept(self) -> None:
        """Persist + accept. The workbench should re-load ai_config.
        json + clear the dispatcher cache when this returns Accepted.

        Phase L3 review fix: previously, save failures were silently
        swallowed — the dialog stayed open with no explanation. Users
        on a read-only %LOCALAPPDATA% (rare but possible on managed
        Windows installs, or when the parent directory is missing
        write permissions) would just see "OK does nothing" and have
        no recourse. Now we surface the underlying error via
        QMessageBox.critical so they can fix the cause.
        """

        from src.services.comparison.ai_classifier import save_ai_config
        try:
            cfg = self._build_config_from_ui()
            save_ai_config(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Saving ai_config.json failed")
            try:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    self,
                    "AI 설정 저장 실패",
                    f"ai_config.json 저장에 실패했습니다.\n\n"
                    f"원인: {exc}\n\n"
                    f"권한 문제일 경우 %LOCALAPPDATA% 디렉토리의 쓰기 권한을 "
                    f"확인하세요. 디스크 공간 부족도 확인해주세요.",
                )
            except Exception:
                # Don't let messagebox errors mask the original failure
                logger.exception("Could not show save-failure dialog")
            return  # don't accept on save failure
        self.accept()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hr() -> QFrame:
    """Horizontal rule separator."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


__all__ = ["AiSettingsDialog"]
