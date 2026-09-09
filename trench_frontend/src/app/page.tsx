"use client";

import { useQuery } from "@tanstack/react-query";

import { getHealth } from "@/lib/api";

export default function Home() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background text-foreground">
      <h1 className="text-3xl font-bold">Trench</h1>
      {isLoading && <p>Checking backend status…</p>}
      {isError && <p className="text-red-500">Backend unreachable</p>}
      {data && (
        <p>
          Backend status: <strong>{data.status}</strong> · DB:{" "}
          <strong>{data.db}</strong>
        </p>
      )}
    </main>
  );
}
