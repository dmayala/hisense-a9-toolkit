/* gpu-probe-fake-egl.c -- host-only fake libEGL_mesa/libGLESv2_mesa for
 * test-gpu-probe-host.sh (review 27 item 5): it FREES the renderer string in
 * eglTerminate, so a probe that keeps glGetString's pointer past teardown
 * trips AddressSanitizer. Renders nothing; touches no device. */
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static char *renderer;
EGLDisplay eglGetDisplay(EGLNativeDisplayType d) { (void)d; return (EGLDisplay)(uintptr_t)1; }
EGLBoolean eglInitialize(EGLDisplay d, EGLint *a, EGLint *b) { (void)d; *a = 1; *b = 5; renderer = strdup("FD610 host fake"); return EGL_TRUE; }
EGLBoolean eglChooseConfig(EGLDisplay d, const EGLint *a, EGLConfig *c, EGLint n, EGLint *o) { (void)d; (void)a; (void)n; *c = (EGLConfig)(uintptr_t)1; *o = 1; return EGL_TRUE; }
EGLContext eglCreateContext(EGLDisplay d, EGLConfig c, EGLContext x, const EGLint *a) { (void)d; (void)c; (void)x; (void)a; return (EGLContext)(uintptr_t)1; }
EGLSurface eglCreatePbufferSurface(EGLDisplay d, EGLConfig c, const EGLint *a) { (void)d; (void)c; (void)a; return (EGLSurface)(uintptr_t)1; }
EGLBoolean eglMakeCurrent(EGLDisplay d, EGLSurface a, EGLSurface b, EGLContext c) { (void)d; (void)a; (void)b; (void)c; return EGL_TRUE; }
EGLint eglGetError(void) { return EGL_SUCCESS; }
EGLBoolean eglTerminate(EGLDisplay d) { (void)d; free(renderer); renderer = 0; return EGL_TRUE; }
const char *eglQueryString(EGLDisplay d, EGLint n) { (void)d; (void)n; return "host fake"; }
__eglMustCastToProperFunctionPointerType eglGetProcAddress(const char *n) { (void)n; return 0; }
const GLubyte *glGetString(GLenum n) { return (const GLubyte *)(n == GL_RENDERER ? renderer : "OpenGL ES 2 host fake"); }
void glClearColor(GLfloat a, GLfloat b, GLfloat c, GLfloat d) { (void)a; (void)b; (void)c; (void)d; }
void glClear(GLbitfield b) { (void)b; }
void glFinish(void) {}
void glFlush(void) {}
void glReadPixels(GLint x, GLint y, GLsizei w, GLsizei h, GLenum f, GLenum t, void *p) { (void)x; (void)y; (void)f; (void)t; unsigned char *q = p; for (int i = 0; i < w * h; i++) { q[4*i] = 64; q[4*i+1] = 128; q[4*i+2] = 192; q[4*i+3] = 255; } }
GLenum glGetError(void) { return GL_NO_ERROR; }
void glGenTextures(GLsizei n, GLuint *t) { for (int i = 0; i < n; i++) t[i] = 1; }
void glBindTexture(GLenum a, GLuint b) { (void)a; (void)b; }
void glGenFramebuffers(GLsizei n, GLuint *t) { for (int i = 0; i < n; i++) t[i] = 1; }
void glBindFramebuffer(GLenum a, GLuint b) { (void)a; (void)b; }
void glFramebufferTexture2D(GLenum a, GLenum b, GLenum c, GLuint d, GLint e) { (void)a; (void)b; (void)c; (void)d; (void)e; }
GLenum glCheckFramebufferStatus(GLenum a) { (void)a; return GL_FRAMEBUFFER_COMPLETE; }
void glViewport(GLint a, GLint b, GLsizei c, GLsizei d) { (void)a; (void)b; (void)c; (void)d; }
void glScissor(GLint a, GLint b, GLsizei c, GLsizei d) { (void)a; (void)b; (void)c; (void)d; }
void glEnable(GLenum a) { (void)a; }
void glDisable(GLenum a) { (void)a; }
void glDeleteTextures(GLsizei n, const GLuint *t) { (void)n; (void)t; }
void glDeleteFramebuffers(GLsizei n, const GLuint *t) { (void)n; (void)t; }
