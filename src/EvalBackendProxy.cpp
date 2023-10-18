/*
 * EvalBackendProxy.cpp
 *
 * Copyright 2023 Matthew Ballance and Contributors
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may 
 * not use this file except in compliance with the License.  
 * You may obtain a copy of the License at:
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software 
 * distributed under the License is distributed on an "AS IS" BASIS, 
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  
 * See the License for the specific language governing permissions and 
 * limitations under the License.
 *
 * Created on:
 *     Author:
 */
#include "EvalBackendProxy.h"
#include "ZuspecSvDpiImp.h"


namespace zsp {
namespace sv {


EvalBackendProxy::EvalBackendProxy() {

}

EvalBackendProxy::~EvalBackendProxy() {

}

void EvalBackendProxy::callFuncReq(
            arl::eval::IEvalThread              *thread,
            arl::dm::IDataTypeFunction          *func_t,
            const std::vector<vsc::dm::ValRef>  &params) {
    zuspec_EvalBackendProxy_callFuncReq(
        reinterpret_cast<chandle>(this),
        reinterpret_cast<chandle>(thread),
        0, // TODO: map func_t to call ID reinterpret_cast<chandle>(func_t),
        reinterpret_cast<const chandle>(
            const_cast<std::vector<vsc::dm::ValRef> *>(&params))
    );
}

void EvalBackendProxy::emitMessage(const std::string &msg) {
    zuspec_EvalBackendProxy_emitMessage(
        reinterpret_cast<chandle>(this),
        msg.c_str()
    );
}

}
}

extern "C" uint64_t zuspec_EvalBackendProxy_new() {
    return reinterpret_cast<uint64_t>(new zsp::sv::EvalBackendProxy());
}

extern "C" int32_t zuspec_ValRefList_size(chandle list_h) {
    std::vector<vsc::dm::ValRef> *list = 
        reinterpret_cast<std::vector<vsc::dm::ValRef> *>(list_h);
    return list->size();
}

extern "C" chandle zuspec_ValRefList_at(
    chandle     list_h,
    int32_t     idx) {
    std::vector<vsc::dm::ValRef> *list = 
        reinterpret_cast<std::vector<vsc::dm::ValRef> *>(list_h);
    return reinterpret_cast<chandle>(&list->at(idx));
}
