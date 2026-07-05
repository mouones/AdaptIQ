# AdaptIQ full audit summary

- Run: 20260611_050404
- API: http://localhost:8000
- Passed: 3
- Failed: 3
- Output folder: C:\Users\mns\Downloads\P_F_E-main\P_F_E\generated\validation_runs\20260611_050404

## Results
- AdaptIQ full audit run: 20260611_050404
- Root: C:\Users\mns\Downloads\P_F_E-main\P_F_E
- API base: http://localhost:8000
- PASS | Preflight: check folders | 0.02s
- PASS | Backend dependencies | 281.55s
- PASS | Backend pytest suite | 0.05s
- FAIL | Start backend and verify health | 92.72s | Backend did not become healthy within 90 seconds. See C:\Users\mns\Downloads\P_F_E-main\P_F_E\generated\validation_runs\20260611_050404\backend_server.log and C:\Users\mns\Downloads\P_F_E-main\P_F_E\generated\validation_runs\20260611_050404\backend_server.err.log
- FAIL | Frontend lint | 0.29s | frontend_npm_install exited with code 1. See C:\Users\mns\Downloads\P_F_E-main\P_F_E\generated\validation_runs\20260611_050404\frontend_npm_install.log
- FAIL | Frontend build | 0.19s | frontend_build exited with code 1. See C:\Users\mns\Downloads\P_F_E-main\P_F_E\generated\validation_runs\20260611_050404\frontend_build.log
