import { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, BookOpen, Code, Zap, Shield } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Vexa API Documentation | Complete API Reference",
  description: "Complete API reference for Vexa meeting transcription platform. Learn how to integrate Vexa into your applications with REST and WebSocket APIs.",
};

export default function DocsPage() {
  return (
    <div className="space-y-8">
      {/* Hero */}
      <div className="space-y-4">
        <h1 className="text-4xl font-bold tracking-tight">Vexa API Documentation</h1>
        <p className="text-xl text-muted-foreground max-w-2xl">
          Complete reference for integrating Vexa meeting transcription into your applications.
          Use REST APIs for meeting management and WebSocket APIs for real-time transcript streaming.
        </p>
      </div>

      {/* Quick Links */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader>
            <Code className="h-8 w-8 mb-2 text-primary" />
            <CardTitle>REST API</CardTitle>
            <CardDescription>Manage meetings, bots, and transcripts</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline" className="w-full">
              <Link href="/docs/rest/meetings">
                Browse REST API
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <Zap className="h-8 w-8 mb-2 text-primary" />
            <CardTitle>WebSocket API</CardTitle>
            <CardDescription>Real-time transcript streaming</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline" className="w-full">
              <Link href="/docs/ws">
                Browse WebSocket API
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <BookOpen className="h-8 w-8 mb-2 text-primary" />
            <CardTitle>Cookbooks</CardTitle>
            <CardDescription>Step-by-step integration guides</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline" className="w-full">
              <Link href="/docs/cookbook/start-transcription">
                View Cookbooks
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <Shield className="h-8 w-8 mb-2 text-primary" />
            <CardTitle>Admin API</CardTitle>
            <CardDescription>User and token management</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline" className="w-full">
              <Link href="/docs/admin/users">
                Browse Admin API
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Getting Started */}
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">Getting Started</h2>
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Authentication</CardTitle>
              <CardDescription>Learn how to authenticate API requests</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground mb-4">
                Vexa uses API keys for authentication. User API keys grant access to meeting operations,
                while Admin API keys provide full system access.
              </p>
              <Button asChild variant="outline">
                <Link href="/docs/auth">
                  Read Authentication Guide
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Quick Start</CardTitle>
              <CardDescription>Send your first transcription bot</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground mb-4">
                Follow our step-by-step cookbook to send a bot to a meeting and receive transcripts.
              </p>
              <Button asChild variant="outline">
                <Link href="/docs/cookbook/start-transcription">
                  Start Tutorial
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* API Overview */}
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">API Overview</h2>
        <div className="prose prose-sm max-w-none">
          <p>
            The Vexa API consists of two main components:
          </p>
          <ul>
            <li>
              <strong>REST API</strong>: For managing meetings, bots, and fetching transcripts.
              All REST endpoints use standard HTTP methods and return JSON responses.
            </li>
            <li>
              <strong>WebSocket API</strong>: For receiving real-time transcript updates as meetings progress.
              Connect once and subscribe to multiple meetings for live streaming.
            </li>
          </ul>
          <p>
            Both APIs require authentication via API keys. User API keys are scoped to individual users,
            while Admin API keys provide system-wide access for user and token management.
          </p>
        </div>
      </div>
    </div>
  );
}

