# Copyright Thales 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
A sample Knowledge Base that mirrors a GitHub or GitLab repository.

Start with `synchronize.py`: one run, from the cursor Fred holds to the cursor
it records. `plan.py` holds every rule that run applies, `git_source.py` the
only code that talks to a repository, and `providers.py` the whole difference
between the two forges.
"""
