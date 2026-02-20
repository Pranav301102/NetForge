declare module "d3" {
  // Minimal subset for this app. Intentional: avoids pulling in @types/d3-* which
  // currently requires TS syntax newer than this repo's TypeScript compiler.
  export function select(el: unknown): any;

  export function forceSimulation(nodes: unknown[]): any;
  export function forceLink(links: unknown[]): any;
  export function forceManyBody(): any;
  export function forceCenter(x: number, y: number): any;
  export function forceCollide(r: number): any;

  export function drag<GElement = any, Datum = any>(): any;
}

