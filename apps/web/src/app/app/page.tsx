"use client";

import { ApiError, api, API_URL, newIdempotencyKey, type Asset, type Generation, type Model } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

export default function CreatePage() {
  return (
    <Suspense fallback={<p className="px-8 py-6 text-mute">Opening the studio.</p>}>
      <Composer />
    </Suspense>
  );
}

function Composer() {
  const queryClient = useQueryClient();
  const requestedId = useSearchParams().get("g");
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api<Model[]>("/api/v1/models"),
  });
  const active = useMemo(
    () => (models.data ?? []).filter((model) => model.enabled),
    [models.data],
  );
  const [modelId, setModelId] = useState("flux");
  const model = (models.data ?? []).find((item) => item.id === modelId) ?? active[0];
  const aspects = model?.version?.capabilities.aspect_ratios ?? ["1:1"];
  const resolutions = model?.version?.capabilities.resolutions ?? ["512"];
  const [prompt, setPrompt] = useState("");
  const [aspect, setAspect] = useState("1:1");
  const [resolution, setResolution] = useState("512");
  const [behavior, setBehavior] = useState("success");
  const [reference, setReference] = useState<Asset | null>(null);
  const [generation, setGeneration] = useState<Generation | null>(null);
  const [error, setError] = useState("");

  const opened = useQuery({
    queryKey: ["generation", requestedId],
    queryFn: () => api<Generation>(`/api/v1/generations/${requestedId}`),
    enabled: Boolean(requestedId),
  });

  useEffect(() => {
    if (!opened.data) return;
    setGeneration(opened.data);
    setPrompt(opened.data.prompt);
    setModelId(opened.data.model);
    const aspectRatio = opened.data.parameters.aspect_ratio;
    const openedResolution = opened.data.parameters.resolution;
    const openedBehavior = opened.data.parameters.fixture_behavior;
    if (typeof aspectRatio === "string") setAspect(aspectRatio);
    if (typeof openedResolution === "string") setResolution(openedResolution);
    if (typeof openedBehavior === "string") setBehavior(openedBehavior);
  }, [opened.data]);

  useEffect(() => {
    if (opened.error instanceof ApiError) setError(opened.error.message);
  }, [opened.error]);

  useEffect(() => {
    if (!aspects.includes(aspect)) setAspect(aspects[0]);
    if (!resolutions.includes(resolution)) setResolution(resolutions[0]);
  }, [aspect, aspects, resolution, resolutions]);

  const watched = useQuery({
    queryKey: ["generation", generation?.id],
    queryFn: () => api<Generation>(`/api/v1/generations/${generation?.id}`),
    enabled: Boolean(generation && (generation.status === "queued" || generation.status === "running")),
    refetchInterval: 800,
  });
  const current = watched.data ?? generation;
  const price =
    resolution === "1024" ? (model?.version?.credit_cost ?? 1) * 2 : (model?.version?.credit_cost ?? 1);

  const create = useMutation({
    mutationFn: async () => {
      const mode = reference ? "image_to_image" : "text_to_image";
      return api<Generation>("/api/v1/generations", {
        headers: { "Idempotency-Key": newIdempotencyKey() },
        json: {
          type: "image",
          mode,
          model: model?.id ?? modelId,
          prompt,
          aspect_ratio: aspect,
          resolution,
          reference_images: reference ? [reference.id] : [],
          fixture_behavior: model?.id === "fixture-image" ? behavior : undefined,
        },
      });
    },
    onSuccess: (next) => {
      setGeneration(next);
      setError("");
      queryClient.invalidateQueries({ queryKey: ["credits"] });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Could not start the generation.");
      queryClient.invalidateQueries({ queryKey: ["credits"] });
    },
  });

  async function onReference(file: File | undefined) {
    if (!file) return;
    const body = new FormData();
    body.set("file", file);
    try {
      const asset = await api<Asset>("/api/v1/uploads", { method: "POST", body });
      setReference(asset);
      setError("");
    } catch (err) {
      setReference(null);
      setError(err instanceof ApiError ? err.message : "Could not attach the reference.");
    }
  }

  return (
    <div className="grid min-h-screen grid-cols-[minmax(320px,420px)_1fr]">
      <form
        className="flex flex-col border-r border-line px-6 py-6"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <p className="text-xs uppercase tracking-[0.22em] text-mute">Create</p>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          required
          maxLength={4000}
          placeholder="Describe the frame."
          className="mt-6 min-h-40 flex-1 resize-none bg-transparent text-lg outline-none placeholder:text-mute"
        />
        <label className="mt-4 text-xs uppercase tracking-[0.18em] text-mute">
          Model
          <select
            value={model?.id ?? modelId}
            onChange={(event) => setModelId(event.target.value)}
            className="mt-2 w-full border border-line bg-ink px-3 py-2 text-sm normal-case tracking-normal text-paper"
          >
            {(models.data ?? []).map((item) => (
              <option key={item.id} value={item.id} disabled={!item.enabled}>
                {item.display_name}
                {item.enabled ? "" : " — soon"}
              </option>
            ))}
          </select>
        </label>
        {model?.id === "flux" && (
          <p className="mt-3 text-xs leading-5 text-mute">
            FLUX.1 schnell. The first run downloads the weights. This 6GB GPU runs one request at a time.
            On a larger GPU, several waiting requests share one pass.
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          {aspects.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setAspect(item)}
              className={`border px-3 py-1 text-sm ${item === aspect ? "border-accent text-accent" : "border-line text-mute"}`}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          {resolutions.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setResolution(item)}
              className={`border px-3 py-1 text-sm ${item === resolution ? "border-accent text-accent" : "border-line text-mute"}`}
            >
              {item}
            </button>
          ))}
        </div>
        <label className="mt-4 text-sm text-mute">
          Reference image
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="mt-2 block w-full text-xs"
            onChange={(event) => onReference(event.target.files?.[0])}
          />
        </label>
        {reference && <p className="mt-2 text-xs text-mute">Attached {reference.width}×{reference.height}</p>}
        {model?.id === "fixture-image" && (
          <label className="mt-4 text-xs uppercase tracking-[0.18em] text-mute">
            Fixture behavior
            <select
              value={behavior}
              onChange={(event) => setBehavior(event.target.value)}
              className="mt-2 w-full border border-line bg-ink px-3 py-2 text-sm normal-case tracking-normal"
            >
              <option value="success">Success</option>
              <option value="retryable">Fail once, then succeed</option>
              <option value="permanent">Fail permanently</option>
              <option value="hang">Stall until timeout</option>
            </select>
          </label>
        )}
        {error && <p className="mt-4 text-sm text-danger">{error}</p>}
        <div className="mt-6 flex items-center justify-between">
          <p className="text-sm text-mute">{price} credit{price === 1 ? "" : "s"}</p>
          <button
            type="submit"
            disabled={create.isPending || !prompt.trim() || !model?.enabled}
            className="bg-accent px-4 py-2 text-sm font-medium text-ink disabled:opacity-50"
          >
            Generate
          </button>
        </div>
      </form>
      <Stage
        generation={current ?? null}
        onChange={(next) => {
          setGeneration(next);
          queryClient.invalidateQueries({ queryKey: ["credits"] });
        }}
      />
    </div>
  );
}

function Stage({
  generation,
  onChange,
}: {
  generation: Generation | null;
  onChange: (generation: Generation) => void;
}) {
  const [cancelError, setCancelError] = useState("");
  const cancel = useMutation({
    mutationFn: () =>
      api<Generation>(`/api/v1/generations/${generation?.id}/cancel`, { method: "POST" }),
    onSuccess: (next) => {
      setCancelError("");
      onChange(next);
    },
    onError: (err) => {
      setCancelError(err instanceof ApiError ? err.message : "Could not cancel.");
    },
  });
  const count =
    generation?.status === "completed"
      ? Math.max(generation.outputs?.length ?? 0, generation.output ? 1 : 0)
      : 0;
  const output = useQuery({
    queryKey: ["output", generation?.id, count],
    enabled: count > 0,
    queryFn: async () => {
      const urls: string[] = [];
      for (let index = 0; index < count; index += 1) {
        const response = await fetch(
          `${API_URL}/api/v1/generations/${generation?.id}/output?index=${index}`,
          {
            credentials: "include",
            headers: { "X-Creativo-Client": "web" },
          },
        );
        if (!response.ok) throw new Error("Output missing");
        urls.push(URL.createObjectURL(await response.blob()));
      }
      return urls;
    },
  });

  useEffect(() => {
    return () => {
      output.data?.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [output.data]);

  return (
    <section className="flex min-h-screen flex-col px-8 py-6">
      <div className="flex items-baseline justify-between">
        <p className="text-xs uppercase tracking-[0.22em] text-mute">Stage</p>
        <p className="text-sm text-paper">{generation ? label(generation.status) : "Empty"}</p>
      </div>
      <div className="mt-6 flex flex-1 items-center justify-center border border-line bg-panel">
        {output.data && output.data.length > 0 ? (
          <div className={`grid w-full gap-3 p-4 ${output.data.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
            {output.data.map((url) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img key={url} src={url} alt={generation?.prompt ?? ""} className="max-h-[70vh] w-full object-contain" />
            ))}
          </div>
        ) : (
          <p className="max-w-sm text-center font-serif text-3xl text-mute">
            {generation ? label(generation.status) : "Nothing in the frame yet."}
          </p>
        )}
      </div>
      {generation && (
        <div className="mt-4 flex items-center justify-between text-sm text-mute">
          <p>{generation.model}</p>
          <div className="flex items-center gap-4">
            {cancelError && <p className="text-danger">{cancelError}</p>}
            {generation.status === "queued" && (
              <button
                type="button"
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
                className="border border-line px-3 py-1 text-paper"
              >
                Cancel
              </button>
            )}
            <p>{generation.failure_message || `${generation.credit_price} credits`}</p>
          </div>
        </div>
      )}
    </section>
  );
}

function label(status: Generation["status"]) {
  if (status === "queued") return "Queued";
  if (status === "running") return "Running";
  if (status === "completed") return "Completed";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
  return "Rejected";
}
