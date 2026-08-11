import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { DataTable } from "../ui/DataTable";
import { Recovery } from "./Recovery";

type Log = { rows: { ts: string; level: string; source: string; message: string; kind: string }[] };

/** 운영 로그 — two tabs, as the page always had: 복구 has the work, 로그 diagnoses it.
 *  Defaults to 복구 for that reason. */
export function Logs() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "recovery";
  const { data, isPending } = useQuery({
    queryKey: ["logs"],
    queryFn: () => getJSON<Log>("/api/ui/logs"),
    refetchInterval: 60_000,
    // 기본 탭은 복구입니다. 그런데 로그는 탭과 상관없이 받아 왔고 1분마다 다시 받았습니다 —
    // 아무도 안 보는 표를 위해 페이지를 열 때마다 요청이 하나 더, 분당 하나 더 나갔습니다.
    enabled: tab === "all",
  });
  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">운영 로그</h1></div>
        <div className="chip-row">
          {([["recovery", "복구"], ["all", "로그"]] as [string, string][]).map(([value, label]) => (
            <button key={value} type="button" className={`chip${tab === value ? " is-active" : ""}`}
                    onClick={() => setParams({ tab: value }, { replace: true })}>{label}</button>
          ))}
        </div>
      </div>
      {tab === "recovery" ? <Recovery /> : (
      <div className="card card--flush">
        <DataTable
          columns={[
            { label: "시각", width: "20%", className: "tnum td-subtle",
              cell: (row) => String(row.ts).slice(0, 19).replace("T", " ") },
            { label: "구분", width: "22%", className: "td-muted",
              cell: (row) => `${row.level} · ${row.source}` },
            { label: "내용", width: "58%", cell: (row) => row.message },
          ]}
          rows={data?.rows ?? []}
          rowKey={(_row, index) => index}
          empty={isPending ? "불러오는 중…" : "기록이 없습니다."}
        />
      </div>
      )}
    </>
  );
}
