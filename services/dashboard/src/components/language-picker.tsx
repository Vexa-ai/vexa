"use client";

import * as React from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  WHISPER_LANGUAGE_CODES,
  WHISPER_LANGUAGE_NAMES,
  getRecentLanguageCodes,
  saveRecentLanguage,
  getLanguageDisplayName,
} from "@/lib/languages";

export interface LanguagePickerProps {
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  triggerClassName?: string;
  placeholder?: string;
  /** Compact trigger (e.g. for mobile header) */
  compact?: boolean;
}

export function LanguagePicker({
  value,
  onValueChange,
  disabled,
  triggerClassName,
  placeholder = "Select language",
  compact = false,
}: LanguagePickerProps) {
  const [open, setOpen] = React.useState(false);
  const [search, setSearch] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement>(null);
  const recentCodes = React.useMemo(() => getRecentLanguageCodes(), [open]);

  const searchLower = search.trim().toLowerCase();
  const filteredCodes = React.useMemo(() => {
    if (!searchLower) return WHISPER_LANGUAGE_CODES;
    return WHISPER_LANGUAGE_CODES.filter(
      (code) =>
        code.toLowerCase().includes(searchLower) ||
        WHISPER_LANGUAGE_NAMES[code]?.toLowerCase().includes(searchLower)
    );
  }, [searchLower]);

  const handleSelect = React.useCallback(
    (code: string) => {
      onValueChange(code);
      saveRecentLanguage(code);
      setOpen(false);
      setSearch("");
    },
    [onValueChange]
  );

  const displayValue = getLanguageDisplayName(value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant={compact ? "ghost" : "outline"}
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            "justify-between font-normal",
            compact && "h-7 px-1.5 text-[9px] border-0 w-auto shrink-0 [&_svg:last-child]:hidden",
            triggerClassName
          )}
        >
          <span className={compact ? "text-[9px] font-medium" : undefined}>
            {displayValue}
          </span>
          {!compact && <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[var(--radix-popover-trigger-width)] min-w-[220px] p-0"
        align="start"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          inputRef.current?.focus();
        }}
      >
        <div className="flex items-center border-b px-2">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input
            ref={inputRef}
            placeholder="Search languages..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="border-0 focus-visible:ring-0 focus-visible:ring-offset-0 h-9"
          />
        </div>
        <ScrollArea className="h-[280px]">
          <div className="p-1">
            {/* Auto-detect always first */}
            <button
              type="button"
              onClick={() => handleSelect("auto")}
              className={cn(
                "flex w-full items-center gap-2 rounded-sm py-2 px-2 text-sm outline-none hover:bg-accent hover:text-accent-foreground",
                value === "auto" && "bg-accent text-accent-foreground"
              )}
            >
              <span className="flex h-4 w-4 items-center justify-center">
                {value === "auto" ? <Check className="h-4 w-4" /> : null}
              </span>
              Auto-detect
            </button>

            {/* Recent (only if not searching and we have recent) */}
            {!searchLower && recentCodes.length > 0 && (
              <>
                <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                  Recent
                </div>
                {recentCodes.map((code) => (
                  <button
                    key={code}
                    type="button"
                    onClick={() => handleSelect(code)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-sm py-2 px-2 text-sm outline-none hover:bg-accent hover:text-accent-foreground",
                      value === code && "bg-accent text-accent-foreground"
                    )}
                  >
                    <span className="flex h-4 w-4 items-center justify-center">
                      {value === code ? <Check className="h-4 w-4" /> : null}
                    </span>
                    {WHISPER_LANGUAGE_NAMES[code]} ({code})
                  </button>
                ))}
                <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                  All languages
                </div>
              </>
            )}

            {/* All / filtered list */}
            {filteredCodes.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No language found.
              </p>
            ) : (
              filteredCodes.map((code) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => handleSelect(code)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm py-2 px-2 text-sm outline-none hover:bg-accent hover:text-accent-foreground",
                    value === code && "bg-accent text-accent-foreground"
                  )}
                >
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                    {value === code ? <Check className="h-4 w-4" /> : null}
                  </span>
                  {WHISPER_LANGUAGE_NAMES[code]} ({code})
                </button>
              ))
            )}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
}
