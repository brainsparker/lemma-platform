import { afterEach, describe, expect, it, vi } from "vitest";
import {
  APP_NOT_GRANTED_CODE,
  ApiError,
  AppNotGrantedError,
  ForbiddenError,
  LEMMA_APP_NOT_GRANTED_MESSAGE_TYPE,
  apiErrorFromStatus,
  isAppNotGrantedError,
} from "../index.js";
import { LemmaClient } from "../client.js";
import { clearTestingToken, setTestingToken } from "../auth.js";

/** An app's page acts for its viewer ∩ the app's own grants. A call outside
 *  the grants is refused `403 APP_NOT_GRANTED`, naming the resource and the
 *  permission an editor adds. An app catches one class and says so; the page
 *  framing it hears about it too, so it can offer the fix. */

afterEach(() => {
  clearTestingToken();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const REFUSAL = {
  code: "APP_NOT_GRANTED",
  message: "Missing permission datastore.table.read on datastore_table 'deals' — this app was not granted it.",
  details: { resource_type: "datastore_table", resource_name: "deals", permission_ids: ["datastore.table.read"] },
};

function refusing(body: unknown = REFUSAL, status = 403) {
  return vi.fn().mockImplementation(async () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

describe("AppNotGrantedError", () => {
  it("is what APP_NOT_GRANTED becomes, and still a ForbiddenError", () => {
    const error = apiErrorFromStatus(403, REFUSAL.message, APP_NOT_GRANTED_CODE, REFUSAL.details);
    expect(error).toBeInstanceOf(AppNotGrantedError);
    expect(error).toBeInstanceOf(ForbiddenError);
    expect(error).toBeInstanceOf(ApiError);
    expect(isAppNotGrantedError(error)).toBe(true);
    const typed = error as AppNotGrantedError;
    expect(typed.resourceType).toBe("datastore_table");
    expect(typed.resourceName).toBe("deals");
    expect(typed.permissionIds).toEqual(["datastore.table.read"]);
    expect(typed.code).toBe("APP_NOT_GRANTED");
    expect(typed.name).toBe("AppNotGrantedError");
  });

  it("reads a grant on the pod itself, and survives details it cannot read", () => {
    const pod = apiErrorFromStatus(403, "no", APP_NOT_GRANTED_CODE, {
      resource_type: "pod", resource_name: "pod", permission_ids: ["agent.execute", 7, ""],
    }) as AppNotGrantedError;
    expect(pod.resourceType).toBe("pod");
    expect(pod.resourceName).toBe("pod");
    expect(pod.permissionIds).toEqual(["agent.execute"]);
    const bare = apiErrorFromStatus(403, "no", APP_NOT_GRANTED_CODE) as AppNotGrantedError;
    expect(bare.resourceType).toBeNull();
    expect(bare.resourceName).toBeNull();
    expect(bare.permissionIds).toEqual([]);
  });

  it("leaves the viewer's own refusal a plain ForbiddenError: no grant on the app fixes it", () => {
    const viewer = apiErrorFromStatus(403, "no", "DELEGATION_EXCEEDS_INVOKER", REFUSAL.details);
    expect(viewer).toBeInstanceOf(ForbiddenError);
    expect(isAppNotGrantedError(viewer)).toBe(false);
    expect(isAppNotGrantedError(apiErrorFromStatus(403, "no", "FORBIDDEN"))).toBe(false);
  });

  it("is what a generated-client call throws, and the framing host is told", async () => {
    setTestingToken("TOKEN");
    vi.stubGlobal("fetch", refusing());
    const postMessage = vi.fn();
    const parent = { postMessage };
    vi.stubGlobal("window", { ...globalThis.window, parent });
    const client = new LemmaClient({ apiUrl: "https://api.test", authUrl: "https://auth.test", podId: "p1" });
    const failure = await client.records.list("deals").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(AppNotGrantedError);
    expect((failure as AppNotGrantedError).resourceName).toBe("deals");
    expect(postMessage).toHaveBeenCalledWith(
      {
        type: LEMMA_APP_NOT_GRANTED_MESSAGE_TYPE,
        resource_type: "datastore_table",
        resource_name: "deals",
        permission_ids: ["datastore.table.read"],
        message: REFUSAL.message,
      },
      "*",
    );
  });

  it("is what a hand-written call throws too", async () => {
    setTestingToken("TOKEN");
    vi.stubGlobal("fetch", refusing({ ...REFUSAL, details: { resource_type: "pod", resource_name: "pod", permission_ids: ["function.execute"] } }));
    const client = new LemmaClient({ apiUrl: "https://api.test", authUrl: "https://auth.test", podId: "p1" });
    const failure = await client.request("GET", "/pods/p1/anything").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(AppNotGrantedError);
    expect((failure as AppNotGrantedError).permissionIds).toEqual(["function.execute"]);
  });

  it("tells nobody when the page is not framed, or the refusal is another one", async () => {
    setTestingToken("TOKEN");
    const postMessage = vi.fn();
    const top = { postMessage } as unknown as Window & { parent: unknown };
    top.parent = top;
    vi.stubGlobal("window", top);
    vi.stubGlobal("fetch", refusing());
    const client = new LemmaClient({ apiUrl: "https://api.test", authUrl: "https://auth.test", podId: "p1" });
    await client.records.list("deals").catch(() => null);
    expect(postMessage).not.toHaveBeenCalled();

    const framedParent = { postMessage: vi.fn() };
    vi.stubGlobal("window", { parent: framedParent });
    vi.stubGlobal("fetch", refusing({ code: "DELEGATION_EXCEEDS_INVOKER", message: "no", details: REFUSAL.details }));
    await client.records.list("deals").catch(() => null);
    expect(framedParent.postMessage).not.toHaveBeenCalled();
  });
});
