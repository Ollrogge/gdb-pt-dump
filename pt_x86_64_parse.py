from pt_x86_64_definitions import *
from pt_common import *

def parse_pml4(phys_mem, addr, offset = -1):
    entries = []
    values = read_page(phys_mem, addr)
    s_entry = None

    for u, value in enumerate(values):
        if (value & 0x1) != 0: # Page must be present
            entry = PML4_Entry(value, u)
            entries.append(entry)

            if offset >= 0 and u == offset:
                s_entry = entry

    return entries, s_entry

def parse_pml4es(phys_mem, pml4es):
    entries = []
    for pml4e in pml4es:
        pdpe, _ = parse_pdp(phys_mem, pml4e)
        entries.extend(pdpe)
    return entries

def parse_pdp(phys_mem, pml4e, offset = -1):
    entries = []
    values = read_page(phys_mem, pml4e.pdp)
    s_entry = None

    for u, value in enumerate(values):
        if (value & 0x1) != 0:
            entry = PDP_Entry(value, pml4e.virt_part, u)
            entries.append(entry)

            if offset >= 0 and u == offset:
                s_entry = entry

    return entries, s_entry

def parse_pdpes(phys_mem, pdpes):
    entries = []
    pages = []
    for pdpe in pdpes:
        if pdpe.one_gig == False:
            pdes, _ = parse_pd(phys_mem, pdpe)
            entries.extend(pdes)
        else:
            page = create_page_from_pdpe(pdpe)
            one_gig_pages.append(page)
    return entries, pages

def parse_pd(phys_mem, pdpe, offset = -1):
    entries = []
    values = read_page(phys_mem, pdpe.pd)
    s_entry = None

    for u, value in enumerate(values):
        if (value & 0x1) != 0:
            entry = PD_Entry(value, pdpe.virt_part, u)
            entries.append(entry)

            if offset >= 0 and u == offset:
                s_entry = entry

    return entries, s_entry

def parse_pdes(phys_mem, pdes):
    entries = []
    pages = []
    for pde in pdes:
        if pde.two_mb == False:
            ptes, _ = parse_pt(phys_mem, pde)
            entries.extend(ptes)
        else:
            page = create_page_from_pde(pde)
            pages.append(page)
    return entries, pages

def parse_pt(phys_mem, pde, offset = -1):
    entries = []
    values = read_page(phys_mem, pde.pt)
    s_entry = None

    for u, value in enumerate(values):
        if (value & 0x1) != 0:
            entry = PT_Entry(value, pde.virt_part, u)
            entries.append(entry)

            if offset >= 0 and u == offset:
                s_entry = entry

    return entries, s_entry

def build_table_hierarchy(phys_mem, a):
    p_offset = a & ((1 << 12) - 1)
    pt_offset = (a >> 12) & ((1 << 9) - 1)
    pd_offset = (a >> 21) & ((1 << 9) - 1)
    pdp_offset = (a >> 30) & ((1 << 9) - 1)
    pml4_offset = (a >> 39) & ((1 << 9) - 1)

    pt_addr = int(gdb.parse_and_eval("$cr3").cast(gdb.lookup_type("long")))

    _, pml4e = parse_pml4(phys_mem, pt_addr, pml4_offset)
    _, pdpe = parse_pdp(phys_mem, pml4e, pdp_offset)

    if pdpe.one_gig:
        print("1 GiB page:")
        page = create_page_from_pdpe(pdpe)
        print(f"    pml4e({hex(pt_addr + pml4_offset * 8)}): {pml4e}")
        print(f"    pdpe({hex(pml4e.pdp + pdp_offset * 8)} / {hex(make_canonical(pml4e.virt_part) + pml4e.pdp + pdp_offset * 8)}): {pdpe}")
        print(page)
        return

    _, pde = parse_pd(phys_mem, pdpe, pd_offset)

    if pde.two_mb:
        print("2 MiB page:")
        page = create_page_from_pde(pde)
        print(f"    pml4e({hex(pt_addr + pml4_offset * 8)}): {pml4e}")
        print(f"    pdpe({hex(pml4e.pdp + pdp_offset * 8)} / {hex(make_canonical(pml4e.virt_part) + pml4e.pdp + pdp_offset * 8)}): {pdpe}")
        print(f"    pde({hex(pdpe.pd + pd_offset * 8)} / {hex(make_canonical(pdpe.virt_part) + pdpe.pd + pd_offset * 8)}): {pde}")
        return

    _, pte = parse_pt(phys_mem, pde, pd_offset)

    p = create_page_from_pte(pte)

    print(f"    pml4e({hex(pt_addr + pml4_offset * 8)}): {pml4e}")
    print(f"    pdpe({hex(pml4e.pdp + pdp_offset * 8)} / {hex(make_canonical(pml4e.virt_part) + pml4e.pdp + pdp_offset * 8)}): {pdpe}")
    print(f"    pde({hex(pdpe.pd + pd_offset * 8)} / {hex(make_canonical(pdpe.virt_part) + pdpe.pd + pd_offset * 8)}): {pde}")
    print(f"    pte({hex(pde.pt + pt_offset * 8)} / {hex(make_canonical(pde.virt_part) + pde.pt + pt_offset * 8)}): {pte}")

def parse_and_print_x86_64_table(cache, phys_mem, args, should_print = True):
    pt_addr = None
    if args.addr:
        pt_addr = int(args.addr[0], 16)
    else:
        pt_addr = int(gdb.parse_and_eval("$cr3").cast(gdb.lookup_type("long")))

    page_ranges = None
    if pt_addr in cache:
        page_ranges = cache[pt_addr]
    else:
        pml4es, _ = parse_pml4(phys_mem, pt_addr)
        pdpes = parse_pml4es(phys_mem, pml4es)
        pdes, one_gig_pages = parse_pdpes(phys_mem, pdpes)
        ptes, two_mb_pages = parse_pdes(phys_mem, pdes)
        small_pages = []
        for pte in ptes:
            small_pages.append(create_page_from_pte(pte))
        page_ranges = optimize(one_gig_pages, two_mb_pages, small_pages, rwxs_semantically_similar)

    # Cache the page table if caching is set.
    # Caching happens before the filter is applied.
    if args.save:
        cache[pt_addr] = page_ranges

    if args.filter:
        filters = []
        for f in args.filter:
            if f == "w":
                filters.append(lambda p: p.w)
            elif f == "_w":
                filters.append(lambda p: not p.w)
            elif f == "x":
                filters.append(lambda p: p.x)
            elif f == "_x":
                filters.append(lambda p: not p.x)
            elif f == "w|x" or f == "x|w":
                filters.append(lambda p: p.x or p.w)
            elif f == "u" or f == "_s":
                filters.append(lambda p: not p.s)
            elif f == "s" or f == "_u":
                filters.append(lambda p: p.s)
            elif f == "ro":
                filters.append(lambda p: not p.x and not p.w)
            elif f == "wb":
                filters.append(lambda p: p.wb)
            elif f == "_wb":
                filters.append(lambda p: not p.wb)
            elif f == "uc":
                filters.append(lambda p: p.uc)
            elif f == "_uc":
                filters.append(lambda p: not p.uc)
            else:
                print(f"Unknown filter: {f}")
                return

        ff = create_compound_filter(filters)
        page_ranges = list(filter(ff, page_ranges))

    if args.range:
        page_ranges = list(filter(lambda page: page.va >= args.range[0] and page.va <= args.range[1], page_ranges))

    if args.has:
        page_ranges = list(filter(lambda page: args.has[0] >= page.va and args.has[0] < page.va + page.page_size, page_ranges))

    if args.after:
        page_ranges = list(filter(lambda page: args.after[0] <= page.va, page_ranges))

    if args.before:
        page_ranges = list(filter(lambda page: args.before[0] > page.va, page_ranges))

    if args.kaslr:
        potential_base_filter = lambda p: p.x and p.s and p.phys[0] % (2 * 1024 * 1024) == 0
        tmp = list(filter(potential_base_filter, page_ranges))
        th = gdb.selected_inferior()
        found_page = None
        for page in tmp:
            first_byte = th.read_memory(page.va, 1)
            if first_byte[0] == b'\x48':
                found_page = page
                break
        if found_page:
            print("Found virtual image base:")
            print("\tVirt: " + str(found_page))
            print("\tPhys: " + hex(found_page.phys[0]))
            first_bytes = th.read_memory(page.va, 32).tobytes()
            page_ranges_subset = filter(lambda page: not page.x and page.s and page.va % 2 * 1024 * 1024 == 0, page_ranges)
            search_res_iter = search_memory(phys_mem, page_ranges_subset, first_bytes, 1, 1, 0)
            if search_res_iter == None:
                print("Phys map was not found")
            else:
                search_res = next(search_res_iter)
                print("Found phys map base:")
                print("\tVirt: " + hex(search_res[0] - found_page.phys[0]) + " in " + str(search_res[1]))
        else:
            print("Failed to find KASLR info")

    if should_print:
        # Compute max len for these varying-len strings in order to print as tabular.
        max_va_len, max_page_size_len = compute_max_str_len(page_ranges)
        conf = PagePrintSettings(va_len = max_va_len, page_size_len = max_page_size_len)
        fmt = f"  {{:>{max_va_len}}} : {{:>{max_page_size_len}}}"
        varying_str = fmt.format("Address", "Length")
        print(bcolors.BLUE + varying_str + "   Permissions          " + bcolors.ENDC)
        for page in page_ranges:
            print(page_to_str(page, conf))

    return page_ranges

