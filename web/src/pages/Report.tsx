import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type RepoMeta, type Summary } from "../api";
import { ErrorNote, Layout, Spinner } from "../components/ui";
import { SummaryView } from "./Interview";

export default function Report() {
  const { token } = useParams();
  const [data, setData] = useState<{ repo: RepoMeta; summary: Summary } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.report(token!).then(setData).catch((e) => setError(e.message));
  }, [token]);

  if (error) return <Layout><ErrorNote message={error} /></Layout>;
  if (!data) return <Layout><Spinner /></Layout>;
  return (
    <>
      <SummaryView summary={data.summary} repoName={`${data.repo.owner}/${data.repo.name}`} shared />
      <div className="mx-auto -mt-16 max-w-3xl px-5 pb-16">
        <Link to="/" className="text-sm text-accent hover:underline">Get interviewed about your own repo →</Link>
      </div>
    </>
  );
}
