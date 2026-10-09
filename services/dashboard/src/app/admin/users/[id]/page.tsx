"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { format, formatDistanceToNow } from "date-fns";
import {
  ArrowLeft,
  User,
  Mail,
  Calendar,
  Bot,
  Key,
  Plus,
  Trash2,
  Copy,
  Check,
  X,
  AlertTriangle,
  Eye,
  EyeOff,
  Pencil,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { useAdminStore } from "@/stores/admin-store";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export default function UserDetailPage() {
  const params = useParams();
  const router = useRouter();
  const idParam = (params as { id?: string | string[] } | null)?.id;
  const userId = Array.isArray(idParam) ? idParam[0] : (idParam ?? "");

  const {
    selectedUser,
    isLoadingUser,
    isCreatingToken,
    error,
    lastCreatedToken,
    fetchUser,
    updateUser,
    createToken,
    revokeToken,
    clearSelectedUser,
    clearLastCreatedToken,
  } = useAdminStore();

  const [isEditing, setIsEditing] = useState(false);
  const [editedName, setEditedName] = useState("");
  const [editedMaxBots, setEditedMaxBots] = useState(3);
  const [isEditingName, setIsEditingName] = useState(false);
  const [tempName, setTempName] = useState("");
  const [isSavingName, setIsSavingName] = useState(false);
  const [isEditingMaxBots, setIsEditingMaxBots] = useState(false);
  const [tempMaxBots, setTempMaxBots] = useState(3);
  const [isSavingMaxBots, setIsSavingMaxBots] = useState(false);
  const [showNewTokenDialog, setShowNewTokenDialog] = useState(false);
  const [copiedTokenId, setCopiedTokenId] = useState<string | null>(null);

  useEffect(() => {
    if (userId) {
      fetchUser(userId);
    }
    return () => {
      clearSelectedUser();
    };
  }, [userId, fetchUser, clearSelectedUser]);

  useEffect(() => {
    if (selectedUser) {
      setEditedName(selectedUser.name || "");
      setTempName(selectedUser.name || "");
      setEditedMaxBots(selectedUser.max_concurrent_bots);
      setTempMaxBots(selectedUser.max_concurrent_bots);
      // Debug: log image_url to verify it's being received
      if (selectedUser.image_url) {
        console.log("User image_url:", selectedUser.image_url);
      } else {
        console.log("User image_url is missing or null");
      }
    }
  }, [selectedUser]);

  useEffect(() => {
    if (lastCreatedToken) {
      setShowNewTokenDialog(true);
    }
  }, [lastCreatedToken]);

  const handleSaveEdit = async () => {
    await updateUser(userId, {
      name: editedName,
      max_concurrent_bots: editedMaxBots,
    });
    setIsEditing(false);
    toast.success("User updated successfully");
  };

  const handleStartEditName = () => {
    setTempName(selectedUser?.name ?? "");
    setIsEditingName(true);
  };

  const handleSaveName = async () => {
    setIsSavingName(true);
    try {
      await updateUser(userId, {
        name: tempName.trim() || undefined,
      });
      // Refresh user data to ensure UI is updated
      await fetchUser(userId);
      setEditedName(tempName.trim());
      setIsEditingName(false);
      toast.success("Name updated successfully");
    } catch (error) {
      toast.error("Failed to update name");
    } finally {
      setIsSavingName(false);
    }
  };

  const handleCancelEditName = () => {
    setTempName(selectedUser?.name ?? "");
    setIsEditingName(false);
  };

  const handleStartEditMaxBots = () => {
    setTempMaxBots(selectedUser?.max_concurrent_bots ?? 3);
    setIsEditingMaxBots(true);
  };

  const handleSaveMaxBots = async () => {
    if (tempMaxBots < 1 || tempMaxBots > 100) {
      toast.error("Max bots must be between 1 and 100");
      return;
    }
    setIsSavingMaxBots(true);
    try {
      await updateUser(userId, {
        max_concurrent_bots: tempMaxBots,
      });
      // Refresh user data to ensure UI is updated
      await fetchUser(userId);
      setEditedMaxBots(tempMaxBots);
      setIsEditingMaxBots(false);
      toast.success("Max bots limit updated successfully");
    } catch (error) {
      toast.error("Failed to update max bots limit");
    } finally {
      setIsSavingMaxBots(false);
    }
  };

  const handleCancelEditMaxBots = () => {
    setTempMaxBots(selectedUser?.max_concurrent_bots ?? 3);
    setIsEditingMaxBots(false);
  };

  const handleCreateToken = async () => {
    await createToken(userId);
  };

  const handleRevokeToken = async (tokenId: string) => {
    await revokeToken(tokenId);
    toast.success("Token revoked successfully");
  };

  const copyToClipboard = async (text: string, tokenId?: string) => {
    await navigator.clipboard.writeText(text);
    if (tokenId) {
      setCopiedTokenId(tokenId);
      setTimeout(() => setCopiedTokenId(null), 2000);
    }
    toast.success("Copied to clipboard");
  };

  if (error) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" onClick={() => router.back()}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back
        </Button>
        <ErrorState error={error} onRetry={() => fetchUser(userId)} />
      </div>
    );
  }

  if (isLoadingUser || !selectedUser) {
    return <UserDetailSkeleton />;
  }

  return (
    <div className="space-y-6">
      {/* Back button */}
      <Button variant="ghost" asChild>
        <Link href="/admin/users">
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Users
        </Link>
      </Button>

      {/* Header */}
      <div className="flex items-start gap-4">
        <Avatar className="h-16 w-16 border-2 border-primary/20">
          <AvatarImage 
            src={selectedUser.image_url || undefined} 
            alt={selectedUser.name || selectedUser.email}
          />
          <AvatarFallback className="bg-gradient-to-br from-primary/20 to-primary/10">
            <span className="text-2xl font-semibold text-primary">
              {selectedUser.name?.[0]?.toUpperCase() || selectedUser.email[0].toUpperCase()}
            </span>
          </AvatarFallback>
        </Avatar>
        <div className="flex-1">
          {isEditingName ? (
            <div className="flex items-center gap-2">
              <Input
                value={tempName}
                onChange={(e) => setTempName(e.target.value)}
                className="text-2xl font-bold h-10 max-w-md"
                placeholder="Name..."
                autoFocus
                disabled={isSavingName}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !isSavingName) {
                    handleSaveName();
                  } else if (e.key === "Escape") {
                    handleCancelEditName();
                  }
                }}
              />
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8"
                onClick={handleSaveName}
                disabled={isSavingName}
              >
                <Check className="h-4 w-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8"
                onClick={handleCancelEditName}
                disabled={isSavingName}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2 group/name">
              <h1 className="text-2xl font-bold">
                {selectedUser.name || "Unnamed User"}
              </h1>
              {!isEditing && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 opacity-0 group-hover/name:opacity-100 transition-opacity"
                  onClick={handleStartEditName}
                >
                  <Pencil className="h-4 w-4" />
                </Button>
              )}
            </div>
          )}
          <p className="text-muted-foreground">{selectedUser.email}</p>
        </div>
        {isEditing && (
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setIsEditing(false)}>
              Cancel
            </Button>
            <Button onClick={handleSaveEdit}>Save</Button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* User Info */}
        <div className="lg:col-span-1 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <User className="h-4 w-4" />
                User Info
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-3">
                <Mail className="h-4 w-4 text-muted-foreground" />
                <div>
                  <p className="text-sm font-medium">Email</p>
                  <p className="text-sm text-muted-foreground">{selectedUser.email}</p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Calendar className="h-4 w-4 text-muted-foreground" />
                <div>
                  <p className="text-sm font-medium">Created</p>
                  <p className="text-sm text-muted-foreground">
                    {format(new Date(selectedUser.created_at), "PPP")}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Bot className="h-4 w-4 text-muted-foreground" />
                <div className="flex-1">
                  <div className="flex items-center gap-2 group/maxbots">
                    <p className="text-sm font-medium">Max Concurrent Bots</p>
                    {!isEditing && !isEditingMaxBots && (
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-6 w-6 opacity-0 group-hover/maxbots:opacity-100 transition-opacity"
                        onClick={handleStartEditMaxBots}
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                  {isEditingMaxBots ? (
                    <div className="flex items-center gap-2 mt-1">
                      <Input
                        type="number"
                        min={1}
                        max={100}
                        value={tempMaxBots}
                        onChange={(e) => setTempMaxBots(parseInt(e.target.value) || 1)}
                        className="h-8 w-24"
                        autoFocus
                        disabled={isSavingMaxBots}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            handleSaveMaxBots();
                          } else if (e.key === "Escape") {
                            handleCancelEditMaxBots();
                          }
                        }}
                      />
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8"
                        onClick={handleSaveMaxBots}
                        disabled={isSavingMaxBots}
                      >
                        <Check className="h-4 w-4" />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-8 w-8"
                        onClick={handleCancelEditMaxBots}
                        disabled={isSavingMaxBots}
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    </div>
                  ) : isEditing ? (
                    <Input
                      type="number"
                      min={1}
                      max={100}
                      value={editedMaxBots}
                      onChange={(e) => setEditedMaxBots(parseInt(e.target.value) || 1)}
                      className="mt-1 h-8 w-24"
                    />
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      {selectedUser.max_concurrent_bots}
                    </p>
                  )}
                </div>
              </div>

              <Separator />

              <div className="flex items-center gap-3">
                <Key className="h-4 w-4 text-muted-foreground" />
                <div>
                  <p className="text-sm font-medium">API Tokens</p>
                  <p className="text-sm text-muted-foreground">
                    {selectedUser.api_tokens?.length || 0} active
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* API Tokens */}
        <div className="lg:col-span-2">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Key className="h-4 w-4" />
                  API Tokens
                </CardTitle>
                <CardDescription>
                  Tokens allow this user to access the Vexa API
                </CardDescription>
              </div>
              <Button onClick={handleCreateToken} disabled={isCreatingToken}>
                <Plus className="mr-2 h-4 w-4" />
                {isCreatingToken ? "Creating..." : "New Token"}
              </Button>
            </CardHeader>
            <CardContent>
              {selectedUser.api_tokens?.length === 0 ? (
                <div className="text-center py-8">
                  <Key className="h-10 w-10 mx-auto text-muted-foreground/50 mb-3" />
                  <p className="text-muted-foreground mb-2">No API tokens yet</p>
                  <p className="text-sm text-muted-foreground">
                    Create a token to allow this user to access the API
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {selectedUser.api_tokens?.map((token) => (
                    <div
                      key={token.id}
                      className="flex items-center gap-3 p-3 rounded-lg border bg-muted/30"
                    >
                      <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center">
                        <Key className="h-5 w-5 text-primary" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium font-mono truncate">
                          {token.token.slice(0, 8)}...{token.token.slice(-8)}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Created {formatDistanceToNow(new Date(token.created_at), { addSuffix: true })}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => copyToClipboard(token.token, token.id)}
                        >
                          {copiedTokenId === token.id ? (
                            <Check className="h-4 w-4 text-green-500" />
                          ) : (
                            <Copy className="h-4 w-4" />
                          )}
                        </Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Revoke Token?</AlertDialogTitle>
                              <AlertDialogDescription>
                                This will immediately revoke access for any application using this token.
                                This action cannot be undone.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction
                                className="bg-destructive hover:bg-destructive/90"
                                onClick={() => handleRevokeToken(token.id)}
                              >
                                Revoke Token
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* New Token Dialog */}
      <Dialog open={showNewTokenDialog} onOpenChange={(open) => {
        if (!open) {
          clearLastCreatedToken();
        }
        setShowNewTokenDialog(open);
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Check className="h-5 w-5 text-green-500" />
              Token Created
            </DialogTitle>
            <DialogDescription>
              Copy this token now. You won&apos;t be able to see it again!
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <div className="p-4 bg-muted rounded-lg">
              <div className="flex items-center gap-2">
                <code className="flex-1 text-sm font-mono break-all">
                  {lastCreatedToken?.token}
                </code>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => lastCreatedToken && copyToClipboard(lastCreatedToken.token)}
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div className="flex items-start gap-2 mt-4 p-3 bg-amber-50 dark:bg-amber-950/20 rounded-lg border border-amber-200 dark:border-amber-800">
              <AlertTriangle className="h-5 w-5 text-amber-500 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-amber-700 dark:text-amber-400">
                Make sure to copy this token now. For security reasons, it cannot be displayed again.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => {
              clearLastCreatedToken();
              setShowNewTokenDialog(false);
            }}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function UserDetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-32" />
      <div className="flex items-start gap-4">
        <Skeleton className="h-16 w-16 rounded-full" />
        <div className="space-y-2">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-32" />
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Skeleton className="h-64" />
        <div className="lg:col-span-2">
          <Skeleton className="h-64" />
        </div>
      </div>
    </div>
  );
}
