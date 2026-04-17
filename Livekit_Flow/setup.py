from setuptools import Extension, setup
from Cython.Build import cythonize


extensions = [
    Extension("agent.metrics", ["agent/metrics.py"]),
    Extension("agent.survey_agent", ["agent/survey_agent.py"]),
    Extension("agent.web_rtc_server", ["agent/web_rtc_server.py"]),
    Extension("smartflow_bridge", ["smart-flo/smartflow_bridge.py"]),
]


setup(
    name="livekit_flow_compiled",
    ext_modules=cythonize(
        extensions,
        compiler_directives={"language_level": "3"},
    ),
)
