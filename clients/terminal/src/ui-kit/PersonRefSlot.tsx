"use client";
/** The supplied person chip, or the name as it is (see `shellProvided.providePersonRef`). */
import { createElement } from "react";
import { suppliedPersonRef, type PersonRefProps } from "./shellProvided";

export function PersonRefSlot(p: PersonRefProps) {
  const C = suppliedPersonRef();
  return C ? createElement(C, p) : <>{p.children ?? p.person.name}</>;
}
