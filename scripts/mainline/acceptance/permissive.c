/* Host only: list actual loaded-policy permissive types, never policy source guesses. */
#include <stdio.h>
#include <sepol/policydb/policydb.h>
int main(int argc, char **argv) {
    policydb_t p;
    policy_file_t f;
    if (argc != 2 || policydb_init(&p)) return 2;
    policy_file_init(&f);
    f.type = PF_USE_STDIO;
    f.fp = fopen(argv[1], "rb");
    if (!f.fp || policydb_read(&p, &f, 0)) return 2;
    ebitmap_node_t *node;
    unsigned bit;
    ebitmap_for_each_positive_bit(&p.permissive_map, node, bit) {
        /* permissive_map stores the one-based type value (not value-1). */
        if (!bit || bit > p.p_types.nprim) return 3;
        puts(p.p_type_val_to_name[bit - 1]);
    }
    fclose(f.fp);
    policydb_destroy(&p);
    return 0;
}
