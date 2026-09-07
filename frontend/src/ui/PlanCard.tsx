import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { getJSON, postForm } from "../lib/api";
import { kst } from "../lib/format";

/** 허브스팟 연락처의 「기본 그룹」 — 플랜 값들.
 *
 *  **같은 값을 두 화면이 다르게 봅니다** (0110, 2026-09-07 운영자 지시):
 *
 *    티켓 상세    → 그 문의가 **들어온 시점**의 플랜. 고치면 그 티켓 행 하나만 바뀝니다.
 *    고객 상세    → **지금** 플랜. 고치면 허브스팟·프로필·워크북으로 나갑니다.
 *
 *  한쪽을 고쳐도 다른 쪽은 안 바뀝니다 — 운영자 지시가 그렇습니다(「값을 따로 관리해서
 *  둘 다 편집은 가능하게」).
 *
 *  **`ui/` 에 있는 이유**: 두 화면이 같은 줄을 그립니다. 카드 껍데기는 고객 상세만 쓰고
 *  (티켓 화면은 한 상자에 합쳐졌습니다) 나머지 셋 — 질의·타입·줄 렌더러 — 은 둘이
 *  나눠 씁니다. 한 화면에 두고 다른 화면이 가져다 쓰면 「화면 A 가 화면 B 를 import
 *  한다」가 되고, 그때부터 하나를 고칠 때마다 다른 하나를 같이 봐야 합니다.
 */
export type RecordRow = {
  key: string; label: string; value: string | null; found: boolean; editable: boolean;
  /** 티켓 상세에도 서는가 (2026-09-07 운영자 지시). 제품 내부 번호(user/space/plan seq)는
   *  고객 상세에만 섭니다 — 티켓 화면이 묻는 것은 「이 문의를 어떻게 판단할까」이고, 거기에
   *  답하는 것은 플랜이지 seq 번호가 아닙니다. **목록은 서버가 줍니다**: 화면에 적으면
   *  필드를 하나 더할 때 한쪽만 늘어나고, 그 어긋남은 두 화면을 나란히 놓기 전에는 안
   *  보입니다. */
  on_ticket: boolean;
};

export type HubSpotRecord = {
  /** 마지막으로 허브스팟에서 받아온 시각. 언제 것이냐가 곧 믿어도 되느냐입니다. */
  synced_at?: string | null;
  /** 이 값이 티켓이 들고 있는 **문의 시점 플랜**인가. 고객 상세는 언제나 거짓이고, 티켓
   *  이라도 이 칸이 생기기 전의 건은 얼려 둔 값이 없어 거짓입니다. */
  frozen?: boolean;
  groups: {
    key: string;
    title: string;
    /** `found: false` 는 「그 회사에 값이 없다」가 아니라 「허브스팟에서 그 속성을 못
     *  찾았다」입니다 — 값 이야기가 아니라 설정 이야기라 화면이 다르게 적습니다. */
    rows: RecordRow[];
    editable: boolean;
  }[];
  error?: string | null;
};

/** 두 컴포넌트가 **같은 질의**를 씁니다 — 티켓 화면은 이 카드와 연락처 정보 카드가 같은
 *  응답의 다른 묶음을 그립니다. 키가 같으면 React Query 가 한 번만 부릅니다. */
export function useHubSpotRecord(contactId?: number, conversationId?: number | null) {
  return useQuery({
    queryKey: ["hubspot-record", contactId, conversationId ?? null],
    queryFn: () => getJSON<HubSpotRecord>(
      `/api/ui/contacts/${contactId}/hubspot-record`
      + (conversationId ? `?conversation_id=${conversationId}` : ""),
    ),
    enabled: !!contactId,
    // 플랜은 화면 하나 읽는 동안 바뀌지 않습니다.
    staleTime: 5 * 60_000,
  });
}

/** 값 한 줄. 고칠 수 없는 줄은 수정 중에도 그냥 글자입니다 — 「국가」는 허브스팟이 접속
 *  IP 로 뽑는 값이고, 못 찾은 필드는 쓸 대상 자체가 없습니다. */
export function RecordValueRow({ row, editing }: { row: RecordRow; editing?: boolean }) {
  if (editing && row.editable) {
    return (
      <div className="info-row">
        <dt><label htmlFor={`hs-${row.key}`}>{row.label}</label></dt>
        <dd style={{ maxWidth: 168 }}>
          <input className="input" id={`hs-${row.key}`} name={row.key}
                 defaultValue={row.value ?? ""} style={{ height: 30, fontSize: 13 }} />
        </dd>
      </div>
    );
  }
  return (
    <div className="info-row">
      <dt>{row.label}</dt>
      <dd className="truncate">
        {!row.found ? <span className="t-subtle">필드를 찾지 못했습니다</span>
         : row.value ?? <span className="t-subtle">—</span>}
      </dd>
    </div>
  );
}

/** 고객 상세의 플랜 카드 — **지금 값**이고, 고치면 허브스팟까지 나갑니다.
 *
 *  티켓 화면에는 이 컴포넌트가 안 섭니다. 거기서는 티켓 정보·플랜·연락처가 **한 상자**라
 *  (2026-09-07 운영자 지시) 그 화면이 `useHubSpotRecord` 와 `RecordValueRow` 로 줄만
 *  그립니다 — 카드 껍데기를 없애려고 합친 자리에 카드를 하나 끼워 넣을 수는 없습니다. */
export function PlanCard({ contactId }: { contactId: number }) {
  const queryClient = useQueryClient();
  const { data } = useHubSpotRecord(contactId);
  const [editing, setEditing] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const fields = Object.fromEntries(new FormData(form) as never) as Record<string, string>;
      // **여기가 바깥으로 나가는 쪽입니다.** 티켓 화면의 저장은 그 티켓 행 하나만 고칩니다 —
      // 거기서 허브스팟에 쓰면 이 화면의 지금 값까지 같이 바뀌어 「따로」가 아닙니다.
      await postForm(`/contacts/${contactId}/hubspot-record`, fields);
    },
    onSuccess: async () => {
      setEditing(null);
      // 「어디에」는 버튼이 아니라 결과가 말합니다 — 누를 때 알아야 할 것은 「저장한다」
      // 하나뿐입니다.
      setNote("저장했습니다 (콘솔 · 허브스팟).");
      // 저장한 뒤 이 질의만 무효화합니다 — 다른 저장들이 쓰는 일괄 무효화에서 이 패널은
      // 일부러 빠져 있습니다(같이 걸면 콘솔의 모든 저장이 열린 탭마다 이걸 다시 읽습니다).
      await queryClient.invalidateQueries({ queryKey: ["hubspot-record"] });
    },
    onError: (error) => setNote(`저장하지 못했습니다: ${String(error)}`),
  });

  if (data?.error) {
    return (
      <div className="card">
        <div className="section-label" style={{ marginBottom: 10 }}>플랜 정보</div>
        <p className="t-xs t-subtle" style={{ margin: 0 }}>{data.error}</p>
      </div>
    );
  }

  return (
    <>
      {data?.groups
        ?.filter((group) => group.key !== "contact")
        .map((group) => {
          const open = editing === group.key;
          return (
            <div className="card" key={group.key}>
              <div className="row-between" style={{ marginBottom: 12 }}>
                <div className="section-label">{group.title}</div>
                {group.editable && (
                  <button type="button" className="btn btn--subtle btn--sm"
                          onClick={() => { setEditing(open ? null : group.key); setNote(null); }}
                          aria-pressed={open}
                          aria-label={open ? `${group.title} 수정 취소` : `${group.title} 수정`}
                          title={open ? "수정 취소" : "수정"}>
                    <Icon name={open ? "x" : "edit"} size={14} />
                  </button>
                )}
              </div>
              <form onSubmit={(event) => { event.preventDefault(); save.mutate(event.currentTarget); }}>
                <dl className="info-list">
                  {group.rows.map((row) => (
                    <RecordValueRow key={row.key} row={row} editing={open} />
                  ))}
                </dl>
                {/* **언제 것인지가 곧 믿어도 되느냐입니다.** 저쪽을 그때그때 읽던 시절에는
                    물어볼 필요가 없던 질문입니다. */}
                {data?.synced_at && (
                  <div className="t-xs t-subtle" style={{ marginTop: 10 }}>
                    마지막 HubSpot 수신 {kst(data.synced_at)}
                  </div>
                )}
                {note && <div className="t-xs" style={{ marginTop: 6 }}>{note}</div>}
                {open && (
                  <button className="btn btn--subtle btn--sm" type="submit"
                          style={{ marginTop: 12, width: "100%" }}
                          disabled={save.isPending} aria-busy={save.isPending || undefined}>
                    {save.isPending ? <><span className="spinner" role="status" /> 저장 중</>
                                    : <><Icon name="check" size={14} /> 저장</>}
                  </button>
                )}
              </form>
            </div>
          );
        })}
    </>
  );
}
