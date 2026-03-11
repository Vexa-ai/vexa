"use client";

import { useState } from "react";
import { Copy, Check, AlertCircle, Info } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { generateCodeExamples, type APIRequest } from "@/lib/docs/code-generator";
import { cn } from "@/lib/utils";

type PathParam = { name: string; type: string; description: string; required?: boolean };

interface APIEndpointDocProps {
  title: string;
  description?: string;
  method: string;
  path: string;
  authType: "user" | "admin" | "none";
  pathParams?: PathParam[];
  queryParams?: Array<{ name: string; type: string; description: string; required?: boolean }>;
  requestBody?: {
    schema: unknown;
    description?: string;
  };
  responseExample?: unknown;
  errorExamples?: Array<{ status: number; body: unknown; description?: string }>;
  notes?: string[];
  dashboardProxy?: string;
}

export function APIEndpointDoc({
  title,
  description,
  method,
  path,
  authType,
  pathParams,
  queryParams,
  requestBody,
  responseExample,
  errorExamples,
  notes,
  dashboardProxy,
}: APIEndpointDocProps) {
  const [copiedLang, setCopiedLang] = useState<string | null>(null);

  // Build API request for code generation
  const apiRequest: APIRequest = {
    method,
    path,
    authType,
    headers: {
      "Content-Type": "application/json",
    },
    query: queryParams?.reduce((acc, param) => {
      if (param.name) acc[param.name] = `your_${param.name}`;
      return acc;
    }, {} as Record<string, string>),
    body: requestBody?.schema,
  };

  const codeExamples = generateCodeExamples(apiRequest);
  const pathParamsSection =
    pathParams?.length ? (
      <Card>
        <CardHeader>
          <CardTitle>Path Parameters</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {pathParams.map((param) => (
              <div key={param.name} className="flex items-start gap-3">
                <code className="bg-muted px-2 py-1 rounded text-sm font-mono shrink-0">
                  {param.name}
                </code>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{param.name}</span>
                    <Badge variant="outline" className="text-xs">
                      {param.type}
                    </Badge>
                    {param.required && (
                      <Badge variant="destructive" className="text-xs">
                        Required
                      </Badge>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">{param.description}</p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    ) : null;

  const handleCopy = async (code: string, language: string) => {
    await navigator.clipboard.writeText(code);
    setCopiedLang(language);
    setTimeout(() => setCopiedLang(null), 2000);
  };


  const methodColors: Record<string, string> = {
    GET: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
    POST: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
    PUT: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
    PATCH: "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
    DELETE: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <Badge className={cn("font-mono", methodColors[method.toUpperCase()] || "bg-gray-100 text-gray-700")}>
            {method.toUpperCase()}
          </Badge>
          <code className="text-lg font-mono bg-muted px-2 py-1 rounded">{path}</code>
        </div>
        <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
        {description && <p className="text-muted-foreground mt-2">{description}</p>}
      </div>

      {/* Dashboard Proxy Info */}
      {dashboardProxy && (
        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            <strong>Dashboard Proxy:</strong> The dashboard calls <code className="bg-muted px-1 rounded">{dashboardProxy}</code> which forwards to this endpoint.
          </AlertDescription>
        </Alert>
      )}

      {/* Authentication */}
      <Card>
        <CardHeader>
          <CardTitle>Authentication</CardTitle>
        </CardHeader>
        <CardContent>
          {authType === "user" && (
            <p className="text-sm">
              Requires <strong>User API Key</strong> in <code className="bg-muted px-1 rounded">X-API-Key</code> header.
            </p>
          )}
          {authType === "admin" && (
            <p className="text-sm">
              Requires <strong>Admin API Key</strong> in <code className="bg-muted px-1 rounded">X-Admin-API-Key</code> header.
            </p>
          )}
          {authType === "none" && (
            <p className="text-sm text-muted-foreground">No authentication required.</p>
          )}
        </CardContent>
      </Card>

      {/* Path Parameters */}
      {pathParamsSection}

      {/* Query Parameters */}
      {queryParams && queryParams.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Query Parameters</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {queryParams.map((param) => (
                <div key={param.name} className="flex items-start gap-3">
                  <code className="bg-muted px-2 py-1 rounded text-sm font-mono shrink-0">
                    {param.name}
                  </code>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{param.name}</span>
                      <Badge variant="outline" className="text-xs">
                        {param.type}
                      </Badge>
                      {param.required && (
                        <Badge variant="destructive" className="text-xs">
                          Required
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground mt-1">{param.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Request Body */}
      {requestBody ? (
        <Card>
          <CardHeader>
            <CardTitle>Request Body</CardTitle>
            {requestBody.description && <CardDescription>{requestBody.description}</CardDescription>}
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg overflow-x-auto text-sm">
              <code>{JSON.stringify(requestBody.schema, null, 2)}</code>
            </pre>
          </CardContent>
        </Card>
      ) : null}

      {/* Code Examples */}
      <Card>
        <CardHeader>
          <CardTitle>Code Examples</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue={codeExamples[0].language}>
            <TabsList className="grid w-full grid-cols-4 lg:grid-cols-7">
              {codeExamples.map((example) => (
                <TabsTrigger key={example.language} value={example.language}>
                  {example.label}
                </TabsTrigger>
              ))}
            </TabsList>
            {codeExamples.map((example) => (
              <TabsContent key={example.language} value={example.language} className="mt-4">
                <div className="relative">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="absolute top-2 right-2"
                    onClick={() => handleCopy(example.code, example.language)}
                  >
                    {copiedLang === example.language ? (
                      <Check className="h-4 w-4" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                  <pre className="bg-muted p-4 rounded-lg overflow-x-auto text-sm">
                    <code>{example.code}</code>
                  </pre>
                </div>
              </TabsContent>
            ))}
          </Tabs>
        </CardContent>
      </Card>

      {/* Response Example */}
      {responseExample ? (
        <Card>
          <CardHeader>
            <CardTitle>Response</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg overflow-x-auto text-sm">
              <code>{JSON.stringify(responseExample, null, 2)}</code>
            </pre>
          </CardContent>
        </Card>
      ) : null}

      {/* Error Examples */}
      {errorExamples && errorExamples.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Error Responses</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {errorExamples.map((error, index) => (
              <div key={index}>
                <div className="flex items-center gap-2 mb-2">
                  <Badge variant="destructive">{error.status}</Badge>
                  {error.description && <span className="text-sm">{error.description}</span>}
                </div>
                <pre className="bg-muted p-4 rounded-lg overflow-x-auto text-sm">
                  <code>{JSON.stringify(error.body, null, 2)}</code>
                </pre>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {/* Notes */}
      {notes && notes.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Notes</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {notes.map((note, index) => (
                <li key={index} className="flex items-start gap-2 text-sm">
                  <AlertCircle className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
                  <span>{note}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

