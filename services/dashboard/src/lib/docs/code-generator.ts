export interface APIRequest {
  method: string;
  path: string;
  authType: "user" | "admin" | "none";
  headers?: Record<string, string>;
  query?: Record<string, string>;
  body?: unknown;
}

export interface CodeExample {
  language: string;
  label: string;
  code: string;
}

const BASE_URL = "https://api.cloud.vexa.ai";

function buildUrl(path: string, query?: Record<string, string>): string {
  const url = `${BASE_URL}${path}`;
  if (!query || Object.keys(query).length === 0) {
    return url;
  }
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value) {
      params.append(key, value);
    }
  });
  return `${url}?${params.toString()}`;
}

function getAuthHeader(authType: "user" | "admin" | "none"): string {
  switch (authType) {
    case "user":
      return "X-API-Key: YOUR_API_KEY";
    case "admin":
      return "X-Admin-API-Key: YOUR_ADMIN_API_KEY";
    case "none":
      return "";
    default:
      return "";
  }
}

function getAuthHeaderJs(authType: "user" | "admin" | "none"): string {
  switch (authType) {
    case "user":
      return "'X-API-Key': 'YOUR_API_KEY'";
    case "admin":
      return "'X-Admin-API-Key': 'YOUR_ADMIN_API_KEY'";
    case "none":
      return "";
    default:
      return "";
  }
}

function generateCurl(apiRequest: APIRequest): string {
  const method = apiRequest.method.toUpperCase();
  const url = buildUrl(apiRequest.path, apiRequest.query);
  const authHeader = getAuthHeader(apiRequest.authType);
  const hasBody = apiRequest.body && method !== "GET";

  let curl = `curl -X ${method} "${url}"`;
  
  if (authHeader) {
    curl += ` \\\n  -H "${authHeader}"`;
  }
  
  if (apiRequest.headers) {
    Object.entries(apiRequest.headers).forEach(([key, value]) => {
      curl += ` \\\n  -H "${key}: ${value}"`;
    });
  }
  
  if (hasBody) {
    const bodyStr = JSON.stringify(apiRequest.body, null, 2);
    curl += ` \\\n  -d '${bodyStr.replace(/'/g, "'\\''")}'`;
  }

  return curl;
}

function generateJavaScript(apiRequest: APIRequest): string {
  const method = apiRequest.method.toUpperCase();
  const url = buildUrl(apiRequest.path, apiRequest.query);
  const authHeader = getAuthHeaderJs(apiRequest.authType);
  const hasBody = apiRequest.body && method !== "GET";

  let headers: string[] = [];
  
  if (authHeader) {
    headers.push(`    ${authHeader}`);
  }
  
  if (apiRequest.headers) {
    Object.entries(apiRequest.headers).forEach(([key, value]) => {
      headers.push(`    '${key}': '${value}'`);
    });
  }

  const headersStr = headers.length > 0 ? `\n  headers: {\n${headers.join(",\n")}\n  }` : "";

  let js = `const response = await fetch('${url}', {`;
  js += `\n  method: '${method}'`;
  
  if (headersStr) {
    js += `,${headersStr}`;
  }
  
  if (hasBody) {
    const bodyStr = JSON.stringify(apiRequest.body, null, 2);
    const indent = "  ";
    const formattedBody = bodyStr
      .split("\n")
      .map((line, i) => (i === 0 ? line : `${indent}${line}`))
      .join("\n");
    js += `,\n  body: ${formattedBody}`;
  }
  
  js += "\n});\n\nconst data = await response.json();";

  return js;
}

function generateTypeScript(apiRequest: APIRequest): string {
  const js = generateJavaScript(apiRequest);
  return js.replace(
    "const response = await fetch",
    "const response: Response = await fetch"
  ).replace(
    "const data = await response.json();",
    "const data = await response.json() as YourResponseType;"
  );
}

function generatePython(apiRequest: APIRequest): string {
  const method = apiRequest.method.toLowerCase();
  const url = buildUrl(apiRequest.path, apiRequest.query);
  const authHeader = getAuthHeaderJs(apiRequest.authType);
  const hasBody = apiRequest.body && method !== "get";

  let headers: string[] = [];
  
  if (authHeader) {
    const [key, value] = authHeader.replace(/'/g, "").split(": ");
    headers.push(`    "${key}": "${value}"`);
  }
  
  if (apiRequest.headers) {
    Object.entries(apiRequest.headers).forEach(([key, value]) => {
      headers.push(`    "${key}": "${value}"`);
    });
  }

  const headersStr = headers.length > 0 ? `\nheaders = {\n${headers.join(",\n")}\n}` : "";

  let python = "import requests\n";
  
  if (headersStr) {
    python += headersStr;
  }
  
  python += `\n\nresponse = requests.${method}('${url}'`;
  
  if (headersStr) {
    python += ",\n    headers=headers";
  }
  
  if (hasBody) {
    const bodyStr = JSON.stringify(apiRequest.body, null, 2);
    const indent = "        ";
    const formattedBody = bodyStr
      .split("\n")
      .map((line, i) => (i === 0 ? line : `${indent}${line}`))
      .join("\n");
    python += `,\n    json=${formattedBody}`;
  }
  
  python += "\n)\n\ndata = response.json()";

  return python;
}

function generateGo(apiRequest: APIRequest): string {
  const method = apiRequest.method.toUpperCase();
  const url = buildUrl(apiRequest.path, apiRequest.query);
  const authHeader = getAuthHeader(apiRequest.authType);
  const hasBody = apiRequest.body && method !== "GET";

  let go = `package main\n\nimport (\n\t"bytes"\n\t"encoding/json"\n\t"fmt"\n\t"io"\n\t"net/http"\n)\n\nfunc main() {\n\turl := "${url}"\n`;
  
  if (hasBody) {
    const bodyStr = JSON.stringify(apiRequest.body, null, 2);
    go += `\tpayload := map[string]interface{}{\n`;
    // Simplified body representation
    go += `\t\t// TODO: Add your payload fields here\n\t}\n`;
    go += `\tjsonData, _ := json.Marshal(payload)\n`;
    go += `\treq, _ := http.NewRequest("${method}", url, bytes.NewBuffer(jsonData))\n`;
  } else {
    go += `\treq, _ := http.NewRequest("${method}", url, nil)\n`;
  }
  
  if (authHeader) {
    const [key, value] = authHeader.split(": ");
    go += `\treq.Header.Set("${key}", "${value}")\n`;
  }
  
  if (apiRequest.headers) {
    Object.entries(apiRequest.headers).forEach(([key, value]) => {
      go += `\treq.Header.Set("${key}", "${value}")\n`;
    });
  }
  
  go += `\n\tclient := &http.Client{}\n`;
  go += `\tresp, _ := client.Do(req)\n`;
  go += `\tdefer resp.Body.Close()\n\n`;
  go += `\tbody, _ := io.ReadAll(resp.Body)\n`;
  go += `\tvar data map[string]interface{}\n`;
  go += `\tjson.Unmarshal(body, &data)\n`;
  go += `\tfmt.Println(data)\n`;
  go += `}`;

  return go;
}

function generateRust(apiRequest: APIRequest): string {
  const method = apiRequest.method.toUpperCase();
  const url = buildUrl(apiRequest.path, apiRequest.query);
  const authHeader = getAuthHeader(apiRequest.authType);
  const hasBody = apiRequest.body && method !== "GET";

  let rust = `use reqwest;\nuse serde_json::json;\n\n#[tokio::main]\nasync fn main() -> Result<(), Box<dyn std::error::Error>> {\n`;
  rust += `\tlet client = reqwest::Client::new();\n`;
  rust += `\tlet url = "${url}";\n`;
  rust += `\tlet mut request = client.request(reqwest::Method::${method}, url);\n`;
  
  if (authHeader) {
    const [key, value] = authHeader.split(": ");
    rust += `\trequest = request.header("${key}", "${value}");\n`;
  }
  
  if (apiRequest.headers) {
    Object.entries(apiRequest.headers).forEach(([key, value]) => {
      rust += `\trequest = request.header("${key}", "${value}");\n`;
    });
  }
  
  if (hasBody) {
    const bodyStr = JSON.stringify(apiRequest.body);
    rust += `\tlet payload = json!(${bodyStr});\n`;
    rust += `\tlet response = request.json(&payload).send().await?;\n`;
  } else {
    rust += `\tlet response = request.send().await?;\n`;
  }
  
  rust += `\n\tlet data: serde_json::Value = response.json().await?;\n`;
  rust += `\tprintln!("{:?}", data);\n`;
  rust += `\tOk(())\n`;
  rust += `}`;

  return rust;
}

function generateJava(apiRequest: APIRequest): string {
  const method = apiRequest.method.toUpperCase();
  const url = buildUrl(apiRequest.path, apiRequest.query);
  const authHeader = getAuthHeader(apiRequest.authType);
  const hasBody = apiRequest.body && method !== "GET";

  let java = `import java.net.http.HttpClient;\n`;
  java += `import java.net.http.HttpRequest;\n`;
  java += `import java.net.http.HttpResponse;\n`;
  java += `import java.net.URI;\n`;
  java += `import java.net.http.HttpRequest.BodyPublishers;\n`;
  
  java += `\npublic class ApiCall {\n`;
  java += `\tpublic static void main(String[] args) throws Exception {\n`;
  java += `\t\tString url = "${url}";\n`;
  
  if (hasBody) {
    const bodyStr = JSON.stringify(apiRequest.body, null, 2);
    java += `\t\tString jsonBody = "${bodyStr.replace(/"/g, '\\"').replace(/\n/g, "\\n")}";\n`;
    java += `\t\tHttpRequest request = HttpRequest.newBuilder()\n`;
    java += `\t\t\t.uri(URI.create(url))\n`;
    java += `\t\t\t.method("${method}", BodyPublishers.ofString(jsonBody))\n`;
  } else if (method === "GET") {
    java += `\t\tHttpRequest request = HttpRequest.newBuilder()\n`;
    java += `\t\t\t.uri(URI.create(url))\n`;
    java += `\t\t\t.GET()\n`;
  } else {
    java += `\t\tHttpRequest request = HttpRequest.newBuilder()\n`;
    java += `\t\t\t.uri(URI.create(url))\n`;
    java += `\t\t\t.method("${method}", BodyPublishers.noBody())\n`;
  }
  
  if (authHeader) {
    const [key, value] = authHeader.split(": ");
    java += `\t\t\t.header("${key}", "${value}")\n`;
  }
  
  if (apiRequest.headers) {
    Object.entries(apiRequest.headers).forEach(([key, value]) => {
      java += `\t\t\t.header("${key}", "${value}")\n`;
    });
  }
  
  java += `\t\t\t.build();\n\n`;
  java += `\t\tHttpClient client = HttpClient.newHttpClient();\n`;
  java += `\t\tHttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());\n`;
  java += `\t\tString data = response.body();\n`;
  java += `\t\tSystem.out.println(data);\n`;
  java += `\t}\n`;
  java += `}`;

  return java;
}

export function generateCodeExamples(apiRequest: APIRequest): CodeExample[] {
  return [
    {
      language: "curl",
      label: "cURL",
      code: generateCurl(apiRequest),
    },
    {
      language: "javascript",
      label: "JavaScript",
      code: generateJavaScript(apiRequest),
    },
    {
      language: "typescript",
      label: "TypeScript",
      code: generateTypeScript(apiRequest),
    },
    {
      language: "python",
      label: "Python",
      code: generatePython(apiRequest),
    },
    {
      language: "go",
      label: "Go",
      code: generateGo(apiRequest),
    },
    {
      language: "rust",
      label: "Rust",
      code: generateRust(apiRequest),
    },
    {
      language: "java",
      label: "Java",
      code: generateJava(apiRequest),
    },
  ];
}
