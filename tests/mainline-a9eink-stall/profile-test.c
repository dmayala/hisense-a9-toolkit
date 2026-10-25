/* Exact accounting tests against the actual kernel profiling header. */
#include "mock.h"
#include <drm/drm_atomic_profile.h>

static u64 clock_ns;
u64 mock_now_ns(void) { return clock_ns; }

#define REQUIRE(c) do { if (!(c)) { \
	fprintf(stderr, "profile FAIL line %d: %s\n", __LINE__, #c); return 1; \
} } while (0)
int main(void)
{
	struct drm_atomic_commit_profile p;
	u64 sum = 0;

	memset(&p, 0xa5, sizeof(p));
	clock_ns = 10000;
	drm_atomic_profile_init(&p);
	REQUIRE(p.phase == DRM_ATOMIC_PROFILE_PREPARE && p.mark_ns == clock_ns);
	for (unsigned int i = 0; i < DRM_ATOMIC_PROFILE_DONE; i++)
		REQUIRE(!p.ns[i]);
	for (unsigned int phase = 1; phase <= DRM_ATOMIC_PROFILE_DONE; phase++) {
		clock_ns += phase * 100;
		drm_atomic_profile_enter(&p, phase);
		REQUIRE(p.ns[phase - 1] == phase * 100);
		sum += p.ns[phase - 1];
	}
	REQUIRE(sum == clock_ns - 10000 && p.phase == DRM_ATOMIC_PROFILE_DONE);
	struct drm_atomic_commit_profile saved = p;
	clock_ns += 100000;
	drm_atomic_profile_enter(&p, DRM_ATOMIC_PROFILE_PREPARE);
	REQUIRE(!memcmp(&p, &saved, sizeof(p))); /* DONE closes all accounting */
	drm_atomic_profile_init(&p);
	saved = p;
	clock_ns += 50;
	drm_atomic_profile_enter(&p, DRM_ATOMIC_PROFILE_DONE + 1);
	REQUIRE(!memcmp(&p, &saved, sizeof(p)));
	clock_ns += 40;
	drm_atomic_profile_enter(&p, DRM_ATOMIC_PROFILE_PREPARE);
	clock_ns += 110;
	drm_atomic_profile_enter(&p, DRM_ATOMIC_PROFILE_FENCES);
	REQUIRE(p.ns[DRM_ATOMIC_PROFILE_PREPARE] == 200);
	for (unsigned int i = 1; i < DRM_ATOMIC_PROFILE_DONE; i++)
		REQUIRE(!p.ns[i]);
	drm_atomic_profile_init(NULL);
	drm_atomic_profile_enter(NULL, DRM_ATOMIC_PROFILE_DONE);
	fprintf(stderr, "PASS: actual profile header exact phase sums, repeated phase, reset and DONE closure\n");
	return 0;
}
