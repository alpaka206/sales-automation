import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable } from "../ui/DataTable";
import { Recovery } from "./Recovery";

// The screens that are a sentence or an iframe. They were four near-identical Jinja
// templates; here they are one component with an argument, because that is all the
// difference between them ever was.
export function Placeholder({ title, message }: { title: string; message: string }) {
  return (
    <>
      <div className="page-header"><div><h1 className="page-title">{title}</h1></div></div>
      <div className="card" style={{ maxWidth: 640 }}>
        <div className="empty">
          <div className="empty__icon"><Icon name="clock" size={24} /></div>
          <div className="empty__text">{message}</div>
        </div>
      </div>
    </>
  );
}

type Log = { rows: { ts: string; level: string; source: string; message: string; kind: string }[] };

/** 운영 로그 — two tabs, as the page always had: 복구 has the work, 로그 diagnoses it.
 *  Defaults to 복구 for that reason. */
export function Logs() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "recovery";
  const { data, isPending } = useQuery({
    queryKey: ["logs"],
    queryFn: () => getJSON<Log>("/api/ui/logs"),
    refetchInterval: 30_000,
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
