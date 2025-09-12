import { log } from '../utils';

export interface WebSocketConfig {
  connectionTimeoutMs: number;
  retryDelayMs: number;
  maxRetries: number;
}

export interface WebSocketEventHandlers {
  onOpen?: (event: Event) => void;
  onMessage?: (event: MessageEvent) => void;
  onError?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
}

export class WebSocketManager {
  private config: WebSocketConfig;
  private socket: WebSocket | null = null;
  private connectionTimeoutHandle: NodeJS.Timeout | null = null;
  private retryCount: number = 0;
  private connecting: boolean = false;

  constructor(config?: Partial<WebSocketConfig>) {
    this.config = {
      connectionTimeoutMs: 5000,
      retryDelayMs: 1000,
      maxRetries: 5,
      ...config
    };
  }

  /**
   * Connect to WebSocket with automatic retry and timeout handling
   */
  async connect(
    url: string,
    handlers: WebSocketEventHandlers = {},
    getNextCandidate?: (failedUrl: string | null) => Promise<string | null>
  ): Promise<WebSocket | null> {
    if (this.connecting) {
      log("[WebSocket] Already connecting, ignoring duplicate connect request");
      return null;
    }

    if (!url) {
      log("[WebSocket] No URL provided");
      if (getNextCandidate) {
        return this.retryWithNextCandidate(null, handlers, getNextCandidate);
      }
      return null;
    }

    this.connecting = true;

    try {
      log(`[WebSocket] Attempting to connect to ${url}`);
      this.socket = new WebSocket(url);

      // Set up connection timeout
      this.connectionTimeoutHandle = setTimeout(() => {
        if (this.socket && this.socket.readyState === WebSocket.CONNECTING) {
          log(`[WebSocket] Connection to ${url} timed out after ${this.config.connectionTimeoutMs}ms`);
          this.socket.close();
        }
      }, this.config.connectionTimeoutMs);

      // Set up event handlers
      this.socket.onopen = (event) => {
        this.clearConnectionTimeout();
        this.connecting = false;
        this.retryCount = 0; // Reset retry count on successful connection
        log(`[WebSocket] Successfully connected to ${url}`);
        handlers.onOpen?.(event);
      };

      this.socket.onmessage = (event) => {
        handlers.onMessage?.(event);
      };

      this.socket.onerror = (event) => {
        this.clearConnectionTimeout();
        log(`[WebSocket] Error connecting to ${url}`);
        handlers.onError?.(event);
      };

      this.socket.onclose = (event) => {
        this.clearConnectionTimeout();
        this.connecting = false;
        log(`[WebSocket] Connection to ${url} closed. Code: ${event.code}, Reason: ${event.reason}`);
        
        // Handle retry logic
        if (this.shouldRetry(event.code)) {
          if (getNextCandidate) {
            this.retryWithNextCandidate(url, handlers, getNextCandidate);
          } else {
            this.retryConnection(url, handlers);
          }
        } else {
          handlers.onClose?.(event);
        }
      };

      return this.socket;
    } catch (error: any) {
      this.clearConnectionTimeout();
      this.connecting = false;
      log(`[WebSocket] Critical error creating WebSocket connection: ${error.message}`);
      
      if (getNextCandidate) {
        return this.retryWithNextCandidate(url, handlers, getNextCandidate);
      } else {
        this.retryConnection(url, handlers);
      }
      
      return null;
    }
  }

  /**
   * Retry connection with exponential backoff
   */
  private async retryConnection(url: string, handlers: WebSocketEventHandlers): Promise<void> {
    if (this.retryCount >= this.config.maxRetries) {
      log(`[WebSocket] Max retries (${this.config.maxRetries}) reached for ${url}`);
      return;
    }

    this.retryCount++;
    const delay = this.config.retryDelayMs * Math.pow(2, this.retryCount - 1);
    
    log(`[WebSocket] Retrying connection to ${url} in ${delay}ms (attempt ${this.retryCount}/${this.config.maxRetries})`);
    
    setTimeout(() => {
      this.connect(url, handlers);
    }, delay);
  }

  /**
   * Retry with next candidate URL
   */
  private async retryWithNextCandidate(
    failedUrl: string | null,
    handlers: WebSocketEventHandlers,
    getNextCandidate: (failedUrl: string | null) => Promise<string | null>
  ): Promise<WebSocket | null> {
    log(`[WebSocket] Getting next candidate after failed URL: ${failedUrl}`);
    
    try {
      const nextUrl = await getNextCandidate(failedUrl);
      
      if (nextUrl) {
        log(`[WebSocket] Got next candidate: ${nextUrl}. Retrying in ${this.config.retryDelayMs}ms`);
        setTimeout(() => {
          this.connect(nextUrl, handlers, getNextCandidate);
        }, this.config.retryDelayMs);
        return this.socket;
      } else {
        log("[WebSocket] No more candidates available. Retrying lookup in 5s");
        setTimeout(async () => {
          const freshUrl = await getNextCandidate(null);
          if (freshUrl) {
            this.connect(freshUrl, handlers, getNextCandidate);
          }
        }, 5000);
        return null;
      }
    } catch (error: any) {
      log(`[WebSocket] Error getting next candidate: ${error.message}`);
      return null;
    }
  }

  /**
   * Check if we should retry based on close code
   */
  private shouldRetry(closeCode: number): boolean {
    // Don't retry for normal closure or authentication failures
    if (closeCode === 1000 || closeCode === 1002 || closeCode === 1003) {
      return false;
    }
    
    // Retry for network issues, server errors, etc.
    return this.retryCount < this.config.maxRetries;
  }

  /**
   * Clear connection timeout
   */
  private clearConnectionTimeout(): void {
    if (this.connectionTimeoutHandle) {
      clearTimeout(this.connectionTimeoutHandle);
      this.connectionTimeoutHandle = null;
    }
  }

  /**
   * Send data through WebSocket
   */
  send(data: string | ArrayBuffer | Blob): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      log("[WebSocket] Cannot send data - WebSocket not open");
      return false;
    }

    try {
      this.socket.send(data);
      return true;
    } catch (error: any) {
      log(`[WebSocket] Error sending data: ${error.message}`);
      return false;
    }
  }

  /**
   * Close WebSocket connection
   */
  close(code?: number, reason?: string): void {
    if (this.socket) {
      this.clearConnectionTimeout();
      this.socket.close(code, reason);
      this.socket = null;
    }
    this.connecting = false;
    this.retryCount = 0;
  }

  /**
   * Get current WebSocket state
   */
  getReadyState(): number | null {
    return this.socket?.readyState || null;
  }

  /**
   * Check if WebSocket is open
   */
  isOpen(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  /**
   * Check if WebSocket is connecting
   */
  isConnecting(): boolean {
    return this.connecting;
  }

  /**
   * Get current retry count
   */
  getRetryCount(): number {
    return this.retryCount;
  }

  /**
   * Reset retry count
   */
  resetRetryCount(): void {
    this.retryCount = 0;
  }

  /**
   * Update configuration
   */
  updateConfig(newConfig: Partial<WebSocketConfig>): void {
    this.config = { ...this.config, ...newConfig };
  }

  /**
   * Get current configuration
   */
  getConfig(): WebSocketConfig {
    return { ...this.config };
  }
}
