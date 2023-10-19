/*
 * ZuspecSv.cpp
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
#include <stdio.h>
#include <fstream>
#include <sstream>
#include "dmgr/FactoryExt.h"
#include "vsc/dm/FactoryExt.h"
#include "vsc/solvers/FactoryExt.h"
#include "zsp/arl/dm/FactoryExt.h"
#include "zsp/arl/eval/FactoryExt.h"
#include "zsp/parser/FactoryExt.h"
#include "zsp/fe/parser/FactoryExt.h"
#include "zsp/ast/IFactory.h"
#include "Actor.h"
#include "EvalBackendProxy.h"
#include "MarkerListener.h"
#include "ZuspecSv.h"
#include "ZuspecSvDpiImp.h"

extern "C" zsp::ast::IFactory *ast_getFactory();

namespace zsp {
namespace sv {



ZuspecSv::ZuspecSv() : 
    m_initialized(false),
    m_loaded(false) {
    m_solver_f = vsc_solvers_getFactory();


}

ZuspecSv::~ZuspecSv() {

}

ZuspecSv *ZuspecSv::inst() {
    if (!m_inst) {
        m_inst = ZuspecSvUP(new ZuspecSv());
    }
    return m_inst.get();
}

bool ZuspecSv::init(
    const std::string       &pss_files,
    bool                    load,
    bool                    debug) {
    if (m_initialized) {
        return true;
    }

    m_dmgr = dmgr_getFactory()->getDebugMgr();

    // Since debug-manager is common infrastructure,
    // only enable if requested. If not requested, 
    // debug may have been enabled by some other library
    if (debug) {
        m_dmgr->enable(debug);
    }
    m_pssfiles = pss_files;

    vsc::dm::IFactory *vsc_dm_f = vsc_dm_getFactory();
    vsc_dm_f->init(m_dmgr);

    arl::dm::IFactory *arl_dm_f = zsp_arl_dm_getFactory();
    arl_dm_f->init(m_dmgr);

    zsp_arl_eval_getFactory()->init(m_dmgr);


    m_ctxt = arl::dm::IContextUP(arl_dm_f->mkContext(
        vsc_dm_f->mkContext()));

    if (load) {
        if (!ensureLoaded()) {
            return false;
        }
    }

    // Process plusargs
    /*
    s_vpi_vlog_info_t info;
    vpi_get_vlog_info(&info);

    for (int32_t i=0; i<info.argc; i++) {
        fprintf(stdout, "Arg: %s\n", info.argv[i]);
    }
     */

    // TODO: Load source files if plusarg speciifed

//    parser::IAstBuilderUP builder(parser_f->mkAstBuilder());

    m_initialized = true;

    return true;
}

bool ZuspecSv::ensureLoaded() {
    char tmp[1024];
    if (m_loaded) {
        return true;
    }

    if (m_pssfiles == "") {
        zuspec_message("No PSS files specified");
        return false;
    }

    snprintf(tmp, sizeof(tmp), "Parsing %s", m_pssfiles.c_str());
    zuspec_message(tmp);

    MarkerListener listener;
    parser::IFactory *parser_f = zsp_parser_getFactory();
    parser_f->init(
        m_dmgr,
        ast_getFactory());
    parser::IAstBuilderUP builder(parser_f->mkAstBuilder(&listener));
    ast::IGlobalScopeUP global(parser_f->getAstFactory()->mkGlobalScope(0));


    std::fstream s;

    s.open(m_pssfiles, std::fstream::in);

    if (!s.is_open()) {
        snprintf(tmp, sizeof(tmp), "Failed to open file %s", m_pssfiles.c_str());
        zuspec_fatal(tmp);
        return false;
    }

    builder->build(
        global.get(),
        &s);
    
    if (listener.hasSeverity(parser::MarkerSeverityE::Error)) {
        zuspec_fatal("Parse errors");
        return false;
    }

    parser::ILinkerUP linker(parser_f->mkAstLinker());
    ast::ISymbolScopeUP scope(linker->link(
        &listener,
        {global.get()}
    ));

    if (listener.hasSeverity(parser::MarkerSeverityE::Error)) {
        zuspec_fatal("Linking errors");
        return false;
    }

    fe::parser::IFactory *fe_parser_f = zsp_fe_parser_getFactory();
    fe_parser_f->init(m_dmgr, parser_f);
    fe::parser::IAst2ArlContextUP builder_ctxt(fe_parser_f->mkAst2ArlContext(
        m_ctxt.get(),
        scope.get(),
        &listener
    ));
    fe::parser::IAst2ArlBuilderUP fe_builder(fe_parser_f->mkAst2ArlBuilder());
    fe_builder->build(
        scope.get(),
        builder_ctxt.get()
    );

    if (listener.hasSeverity(parser::MarkerSeverityE::Error)) {
        zuspec_fatal("Data-model build errors");
        return false;
    }


    m_loaded = true;

    return true;
}

ZuspecSvUP ZuspecSv::m_inst;

}
}

/****************************************************************************
 * DPI Interface
 ****************************************************************************/
static char dpiStrBuf[1024];

extern "C" uint32_t zuspec_init(
    const char      *pss_files,
    int             load,
    int             debug) {
    return zsp::sv::ZuspecSv::inst()->init(pss_files, load, debug);
}

extern "C" chandle zuspec_Actor_new(
    const char          *seed,
    const char          *comp_t_s,
    const char          *action_t_s,
    uint64_t             backend_h) {
    char tmp[1024];
    zsp::sv::ZuspecSv *zsp_sv = zsp::sv::ZuspecSv::inst();
    zsp::arl::dm::IContext *ctxt = zsp_sv->ctxt();
    zsp::sv::EvalBackendProxy *backend = reinterpret_cast<zsp::sv::EvalBackendProxy *>(backend_h);

    if (!zsp_sv->ensureLoaded()) {
        zuspec_fatal("Failed to load PSS files");
        return 0;
    }

    zsp::arl::dm::IDataTypeComponent *comp_t = ctxt->findDataTypeComponent(comp_t_s);
    if (!comp_t) {
        snprintf(tmp, sizeof(tmp), "Failed to find component %s", comp_t_s);
        zuspec_fatal(tmp);
        return 0;
    }

    zsp::arl::dm::IDataTypeAction *action_t = ctxt->findDataTypeAction(action_t_s);

    if (!action_t) {
        snprintf(tmp, sizeof(tmp), "Failed to find action %s", action_t_s);
        zuspec_fatal(tmp);
        return 0;
    }

    zsp::sv::Actor *actor = new zsp::sv::Actor(
        ctxt,
        seed,
        comp_t,
        action_t,
        backend);

    return reinterpret_cast<chandle>(actor);
}

extern "C" int32_t zuspec_Actor_eval(
    chandle     actor_h) {
    return reinterpret_cast<zsp::sv::Actor *>(actor_h)->eval();
}

extern "C" uint32_t zuspec_Actor_registerFunctionId(
    chandle     actor_h,
    const char  *name,
    int32_t     id) {
    return reinterpret_cast<zsp::sv::Actor *>(actor_h)->registerFunctionId(name, id);
}

extern "C" int32_t zuspec_Actor_getFunctionId(
    chandle     actor_h,
    chandle     func_h) {
    return reinterpret_cast<zsp::sv::Actor *>(actor_h)->getFunctionId(
        reinterpret_cast<zsp::arl::dm::IDataTypeFunction *>(func_h));
}

extern "C" const char *zuspec_DataTypeFunction_name(
    chandle     func_h) {
    strcpy(dpiStrBuf, 
        reinterpret_cast<zsp::arl::dm::IDataTypeFunction *>(func_h)->name().c_str());
    return dpiStrBuf;
}

extern "C" void zuspec_EvalThread_setVoidResult(
    chandle     thread_h) {
    reinterpret_cast<zsp::arl::eval::IEvalThread *>(thread_h)->setVoidResult();
}

extern "C" void zuspec_EvalThread_setIntResult(
    chandle      thread_h,
    int64_t      value,
    int          is_signed,
    int          width) {
    zsp::arl::eval::IEvalThread *thread = 
        reinterpret_cast<zsp::arl::eval::IEvalThread *>(thread_h);
    thread->setResult(thread->mkValRefInt(value, is_signed, width));
}
