/**
 * Registration control utilities
 */

export interface RegistrationConfig {
  allowRegistrations: boolean;
  allowedDomains: string[] | null; // null means all domains allowed
}

/**
 * Get registration configuration from environment variables
 */
export function getRegistrationConfig(): RegistrationConfig {
  const allowRegistrations = process.env.ALLOW_REGISTRATIONS !== "false";

  const domainsEnv = process.env.ALLOWED_EMAIL_DOMAINS;
  const allowedDomains = domainsEnv
    ? domainsEnv.split(",").map((d) => d.trim().toLowerCase()).filter(Boolean)
    : null;

  return {
    allowRegistrations,
    allowedDomains: allowedDomains && allowedDomains.length > 0 ? allowedDomains : null,
  };
}

/**
 * Check if an email domain is allowed
 */
export function isEmailDomainAllowed(email: string, config: RegistrationConfig): boolean {
  // If no domain restrictions, all domains are allowed
  if (!config.allowedDomains) {
    return true;
  }

  const emailDomain = email.split("@")[1]?.toLowerCase();
  if (!emailDomain) {
    return false;
  }

  return config.allowedDomains.includes(emailDomain);
}

/**
 * Validate email for registration
 * Returns null if valid, or an error message if invalid
 */
export function validateEmailForRegistration(
  email: string,
  userExists: boolean,
  config: RegistrationConfig
): string | null {
  // If user exists, they can always login (no registration needed)
  if (userExists) {
    return null;
  }

  // Check if registrations are allowed
  if (!config.allowRegistrations) {
    return "New registrations are disabled. Please contact an administrator.";
  }

  // Check domain restrictions
  if (!isEmailDomainAllowed(email, config)) {
    const domains = config.allowedDomains!.join(", ");
    return `Registration is only allowed for the following domains: ${domains}`;
  }

  return null;
}
