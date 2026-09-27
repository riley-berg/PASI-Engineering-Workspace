declare namespace PASIUserScript {
  interface ScriptInfo {
    id: string;
    name: string;
    namespace: string;
    version: string;
    description: string;
    matches: string[];
    excludes: string[];
    grants: string[];
    connects: string[];
    runAt: "document_start" | "document_end" | "document_idle";
    noframes: boolean;
  }
  interface StorageChange<T = unknown> {
    key: string;
    oldValue?: T;
    newValue?: T;
    remote: boolean;
  }
  interface HttpResponse {
    ok: boolean;
    status: number;
    statusText: string;
    url: string;
    headers: Record<string, string>;
    body: string;
  }
  interface DownloadOptions {
    url: string;
    filename?: string;
    saveAs?: boolean;
  }
  interface NotificationOptions {
    title?: string;
    message: string;
  }
  const info: Readonly<ScriptInfo>;
  function getValue<T>(key: string, defaultValue?: T): Promise<T | undefined>;
  function setValue<T>(key: string, value: T): Promise<void>;
  function deleteValue(key: string): Promise<void>;
  function listValues(): Promise<string[]>;
  function addValueChangeListener<T>(key: string, listener: (key: string, oldValue: T | undefined, newValue: T | undefined, remote: boolean) => void): Promise<string>;
  function removeValueChangeListener(listenerId: string): Promise<void>;
  function openInTab(url: string, options?: {active?: boolean}): Promise<{tab_id?: number; url: string}>;
  function notification(details: NotificationOptions | string, title?: string): Promise<string>;
  function setClipboard(text: string): Promise<{ok: boolean}>;
  function download(details: DownloadOptions | string, name?: string): Promise<number>;
  function registerMenuCommand(name: string, callback: (info: unknown) => void, options?: {accessKey?: string}): Promise<string>;
  function xmlhttpRequest(config: {
    url: string;
    method?: string;
    headers?: Record<string, string>;
    data?: string;
    timeout?: number;
    onload?: (response: HttpResponse) => void;
    onerror?: (error: unknown) => void;
  }): Promise<HttpResponse>;
  function fetch(input: string | {url: string; method?: string; headers?: Record<string, string>; body?: string}, init?: {method?: string; headers?: Record<string, string>; body?: string; timeout?: number}): Promise<HttpResponse & {text(): Promise<string>; json<T = unknown>(): Promise<T>}>;
  const network: {
    addRule(rule: unknown): Promise<unknown>;
    removeRule(ruleId: string): Promise<unknown>;
    listRules(): Promise<unknown>;
  };
  function trackCleanup(callback: () => void): () => boolean;
  const unsafeWindow: Window & typeof globalThis;
}

declare const GM_info: {readonly script: Readonly<PASIUserScript.ScriptInfo>};
declare const GM_getValue: typeof PASIUserScript.getValue;
declare const GM_setValue: typeof PASIUserScript.setValue;
declare const GM_deleteValue: typeof PASIUserScript.deleteValue;
declare const GM_listValues: typeof PASIUserScript.listValues;
declare const GM_addValueChangeListener: typeof PASIUserScript.addValueChangeListener;
declare const GM_removeValueChangeListener: typeof PASIUserScript.removeValueChangeListener;
declare const GM_openInTab: typeof PASIUserScript.openInTab;
declare const GM_notification: typeof PASIUserScript.notification;
declare const GM_setClipboard: typeof PASIUserScript.setClipboard;
declare const GM_download: typeof PASIUserScript.download;
declare const GM_registerMenuCommand: typeof PASIUserScript.registerMenuCommand;
declare const GM_xmlhttpRequest: typeof PASIUserScript.xmlhttpRequest;


declare const GM_fetch: typeof PASIUserScript.fetch;
declare const GM_webRequest: typeof PASIUserScript.network;
