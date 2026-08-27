"""Code-enforced drafting language for reply bodies.

Hard rule: the draft is written in the **inquiry's language** — the language the mail
will actually leave in — and :func:`ensure_language` puts it there if the drafting model
wrote in another one, washing the layout regardless of model output.

**예전에는 반대였습니다.** 초안은 늘 한국어로 쓰였고(``ensure_korean``), 운영자가
`번역하기` 를 누르면 flash 가 그것을 고객 언어로 되돌렸습니다. 그러면 정책 문서에 운영자가
**영어로 써 둔 완성된 메일**이 고객에게 그대로 갈 길이 없습니다: 모델이 그 문장을 한국어로
다시 쓰고, 번역기가 그 한국어를 영어로 되돌립니다. 실제로 `Hi [Name], Thanks for reaching
out to Perso Dubbing…` 이 `Hello, Ivan. Thank you for your inquiry about Perso Dubbing.`
으로, `Looking forward to helping you get started! Cheers, Untae Bae` 가 `Thank you.` 로
나갔습니다 (2026-08-26, msg 64). 문서의 **내용**은 살아남고 **문장**은 못 살아남습니다.

운영자가 한국어로 검토하던 자리는 없어지지 않았습니다 — 한국어 대역을 초안 때 한 번 만들어
``messages.body_ko`` 에 **저장하고**, 검토 화면이 본문 옆에 같이 그립니다. 볼 때마다 모델을
부르지 않습니다.

(The separate send-time guarantee that a reply leaves in the inquiry's language lives in
:func:`src.integrations.senders.enforce_send_language`, which works off the message's
stored language/target metadata.)
"""

from __future__ import annotations

from ..common.textwash import text_wash
from .client import LLMClient
from .translate import is_mostly_korean, to_korean, translate_to


def ensure_language(body: str | None, target: str | None, *, llm: LLMClient | None = None) -> str:
    """Return ``body`` in ``target`` (washed). Translates only if it isn't already.

    **잴 수 있는 것은 한국어냐 아니냐 하나입니다.** 스크립트로 영어와 베트남어를 가르지는
    못하고, 그럴 필요도 없습니다: 이 자리에서 실제로 일어나는 실수는 하나 — 프롬프트도
    참고 문서도 한국어인 탓에 모델이 **한국어로** 써 버리는 것입니다. 그래서 한국어 목표는
    「한국어가 아니면 옮긴다」, 그 외 목표는 「한국어면 옮긴다」로 대칭입니다.

    번역이 실패하면 원문을 그대로 돌려줍니다 — 부르는 쪽이 그 결과를 보고 언어 라벨을
    정하므로(``_draft_reply``), 라벨이 거짓이 되지 않습니다. 그리고 발송 관문
    (``enforce_send_language``)이 한 번 더 막습니다.
    """
    washed = text_wash(body)
    if not washed:
        return ""
    code = (target or "ko").strip().lower()[:2] or "ko"
    korean = is_mostly_korean(washed)
    if code == "ko":
        if korean:
            return washed
        return text_wash(to_korean(washed, llm=llm)) or washed
    if not korean:
        return washed
    return text_wash(translate_to(washed, code, llm=llm)) or washed


def korean_reading(body: str | None, *, llm: LLMClient | None = None) -> str:
    """검토 화면이 본문 옆에 그릴 한국어 대역. 이미 한국어면 빈 문자열입니다.

    **초안 때 한 번 만들어 행에 저장합니다** (``messages.body_ko``). 본문이 바뀌지 않는
    한 번역도 바뀌지 않으므로, 화면을 열 때마다 모델을 부를 이유가 없습니다 — 0045 가
    고객 문의에 같은 이유로 같은 칸을 만들었습니다.

    주소와 토큰은 ``translate.to_korean`` 이 지킵니다. 그래서 링크·금액 가드가 전부 끝난
    **뒤에** 부릅니다: 두 벌이 같은 링크, 같은 문장을 들고 있어야 대조가 됩니다.
    """
    washed = text_wash(body)
    if not washed or is_mostly_korean(washed):
        return ""
    return text_wash(to_korean(washed, llm=llm))
