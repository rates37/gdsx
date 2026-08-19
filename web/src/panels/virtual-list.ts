// A minimal fixed-row-height virtual scroller. The netlist browser holds up
// to a few thousand nets; rendering all of them as DOM nodes is the kind of
// thing that works fine in a demo and then chokes the first time someone
// opens it on the real puzzle. No pooling -- rebuilding ~40 visible rows on
// scroll is cheap enough that pooling would be optimising the wrong thing.

export class VirtualList<T> {
  private items: T[] = [];
  private readonly rows: HTMLDivElement;
  private readonly sizer: HTMLDivElement;

  constructor(
    private readonly viewport: HTMLElement,
    private readonly rowHeight: number,
    private readonly renderRow: (item: T, index: number) => HTMLElement,
  ) {
    viewport.classList.add("vlist");
    this.sizer = document.createElement("div");
    this.sizer.className = "vlist-sizer";
    this.rows = document.createElement("div");
    this.rows.className = "vlist-rows";
    this.sizer.append(this.rows);
    viewport.append(this.sizer);
    viewport.addEventListener("scroll", () => this.render());
    new ResizeObserver(() => this.render()).observe(viewport);
  }

  setItems(items: T[]): void {
    this.items = items;
    this.sizer.style.height = `${items.length * this.rowHeight}px`;
    this.render();
  }

  /** Re-render the currently visible rows without changing `items` -- for
   *  when a row's own appearance depends on outside state (e.g. whether
   *  it's the highlighted net). */
  refresh(): void {
    this.render();
  }

  private render(): void {
    const scrollTop = this.viewport.scrollTop;
    const viewH = this.viewport.clientHeight || 1;
    const overscan = 8;
    const first = Math.max(0, Math.floor(scrollTop / this.rowHeight) - overscan);
    const last = Math.min(
      this.items.length,
      Math.ceil((scrollTop + viewH) / this.rowHeight) + overscan,
    );
    this.rows.style.transform = `translateY(${first * this.rowHeight}px)`;
    this.rows.replaceChildren();
    for (let i = first; i < last; i++) {
      const el = this.renderRow(this.items[i], i);
      el.style.height = `${this.rowHeight}px`;
      this.rows.append(el);
    }
  }
}